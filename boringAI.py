try:
    from malmo import MalmoPython
except:
    import MalmoPython

import os
import sys
import time
import json
import random
from tqdm import tqdm
from collections import deque,defaultdict
import matplotlib.pyplot as plt
import numpy as np
from numpy.random import randint

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader


# Hyperparameters
TUNNEL_LEN_START = 20
TUNNEL_LEN_DELTA = 0
SIZE = 50
OBS_SIZE = 2#only forward direction CURRENT AND FORWARD = one each
MAX_EPISODE_STEPS = 5
MAX_GLOBAL_STEPS = 5000
REPLAY_BUFFER_SIZE = 10000
EPSILON_DECAY = .999
MIN_EPSILON = .1
BATCH_SIZE = 128
GAMMA = .9
TARGET_UPDATE = 100
LEARNING_RATE = 1e-4
START_TRAINING = 250
LEARN_FREQUENCY = 1

ACTION_DICT = {
    0: ['hotbar.1 1','hotbar.1 0']  ,#switch to pickaxe
    1: ['hotbar.2 1',' hotbar.2 0'], #switch to shovel
    2: ['hotbar.3 1','hotbar.3 0'] #switch to axe
}


class QNetwork(nn.Module):
    def __init__(self, obs_size, action_size, hidden_size=50):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(np.prod(obs_size), hidden_size),
                                 nn.ReLU(),
                                 nn.Linear(hidden_size, action_size))

    def forward(self, obs):
        batch_size = obs.shape[0]
        obs_flat = obs.view(batch_size, -1)
        return self.net(obs_flat)


def GetMissionXML(num_episode):
    block_type = ['dirt', 'stone','log']
    tunnel_xml = ''
    tunnel_len = TUNNEL_LEN_START + num_episode*TUNNEL_LEN_DELTA
    for i in range(1, tunnel_len + 1):
        tunnel_xml += "<DrawBlock x=\'0\' y=\'2\' z=\'" + str(i) + "\' type=\'" + random.choice(block_type) + "\' />"
    for i in range(-5, 6):
        if i%2 == 0:
            tunnel_xml += "<DrawBlock x=\'" + str(i) + "\' y=\'1\' z=\'"+str(tunnel_len +1) + "\' type=\'coal_block\' />"
        else:
            tunnel_xml += "<DrawBlock x=\'" + str(i) + "\' y=\'1\' z=\'"+str(tunnel_len+1) + "\' type=\'quartz_block\' />"
    for i in range(-5, 6):
        for j in range(2,5):
            tunnel_xml += "<DrawBlock x=\'" + str(i) + "\' y=\'" + str(j) + "\' z=\'1\' type=\'glass\' />"
    for i in range(1, tunnel_len + 1):
        for j in range(2, 5):
            tunnel_xml += "<DrawBlock x=\'-5\' y=\'" + str(j) + "\' z=\'"+ str(i) + "\' type=\'glass\' />"
            tunnel_xml += "<DrawBlock x=\'5\' y=\'" + str(j) + "\' z=\'"+ str(i) + "\' type=\'glass\' />"


    tunnel_xml += "<DrawBlock x=\'0\' y=\'2\' z=\'1\' type=\'air\' />"
    tunnel_xml += "<DrawBlock x=\'0\' y=\'3\' z=\'1\' type=\'air\' />"

    return '''<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
            <Mission xmlns="http://ProjectMalmo.microsoft.com" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">

                <About>
                    <Summary>Tunnel Crawler</Summary>
                </About>

                <ServerSection>
                    <ServerInitialConditions>
                        <Time>
                            <StartTime>12000</StartTime>
                            <AllowPassageOfTime>false</AllowPassageOfTime>
                        </Time>
                        <Weather>clear</Weather>
                    </ServerInitialConditions>
                    <ServerHandlers>
                        <FlatWorldGenerator generatorString="3;7,2;1;"/>
                        <DrawingDecorator>''' + \
                            "<DrawCuboid x1='{}' x2='{}' y1='2' y2='2' z1='{}' z2='{}' type='air'/>".format(-SIZE, SIZE, -SIZE, SIZE) + \
                            "<DrawCuboid x1='{}' x2='{}' y1='1' y2='1'z1='{}'z2='{}' type='prismarine'/>".format(-SIZE, SIZE, -SIZE, SIZE) + \
                            tunnel_xml + \
                            '''<DrawBlock x='0'  y='2' z='0' type='air' />
                            <DrawBlock x='0'  y='1' z='0' type='prismarine' />
                        </DrawingDecorator>
                        <ServerQuitWhenAnyAgentFinishes/>
                    </ServerHandlers>
                </ServerSection>

                <AgentSection mode="Survival">
                    <Name>Tunnel Crawler</Name>
                    <AgentStart>
                        <Placement x="0.5" y="2" z="0.5" pitch="45" yaw="0"/>
                        <Inventory>
                            <InventoryItem slot="0" type="diamond_pickaxe"/>
                            <InventoryItem slot="1" type="diamond_shovel"/>
                            <InventoryItem slot="2" type="diamond_axe"/>
                        </Inventory>
                    </AgentStart>
                    <AgentHandlers>
                        <ContinuousMovementCommands/>
                        <InventoryCommands/>
                        <ObservationFromFullInventory flat="false"/>
                        <ObservationFromFullStats/>
                        <RewardForTimeTaken initialReward = "0"  delta = "1" density = "MISSION_END"/>
                        <ObservationFromGrid>
                            <Grid name="floorAll">
                                <min x = "0" y = "-1" z = "0"/>
                                <max x="0" y="0" z="'''+str(int(OBS_SIZE)-1)+'''"/>
                            </Grid>
                        </ObservationFromGrid>
                         <AgentQuitFromTouchingBlockType>
                            <Block type="coal_block"/>
                        </AgentQuitFromTouchingBlockType>
                    </AgentHandlers>
                </AgentSection>
            </Mission>'''


def get_action(obs, q_network, epsilon, allow_break_action):
    p = np.random.random()
    if p < epsilon:
        return randint(0,len(ACTION_DICT))

    # Prevent computation graph from being calculated
    with torch.no_grad():
        # Calculate Q-values fot each action
        obs_torch = torch.tensor(obs.copy(), dtype=torch.float).unsqueeze(0)
        action_values = q_network(obs_torch)


        # Remove attack/mine from possible actions if not facing a diamond
        if not allow_break_action:
            action_values[0, 1] = -float('inf')

        # Select action with hig)est Q-value
        action_idx = torch.argmax(action_values).item()

    return action_idx


def init_malmo(agent_host, num_episode):
    my_mission = MalmoPython.MissionSpec(GetMissionXML(num_episode), True)
    my_mission_record = MalmoPython.MissionRecordSpec()
    my_mission.requestVideo(800, 500)
    my_mission.setViewpoint(1)

    max_retries = 3
    my_clients = MalmoPython.ClientPool()
    my_clients.add(MalmoPython.ClientInfo('127.0.0.1', 10000)) # add Minecraft machines here as available

    for retry in range(max_retries):
        try:
            agent_host.startMission( my_mission, my_clients, my_mission_record, 0, "Secret Tunnel" )
            break
        except RuntimeError as e:
            if retry == max_retries - 1:
                print("Error starting mission:", e)
                exit(1)
            else:
                time.sleep(2)

    return agent_host


def get_observation(world_state):
    obs = np.zeros((2,OBS_SIZE))

    while world_state.is_mission_running:
        time.sleep(0.1)
        world_state = agent_host.getWorldState()
        if len(world_state.errors) > 0:
            raise AssertionError('Could not load grid.')

        if world_state.number_of_observations_since_last_state > 0:
            # First we get the json from the observation API
            msg = world_state.observations[-1].text
            observations = json.loads(msg)

            # Get observation
            grid = observations['floorAll']
            block_dict = defaultdict(lambda:0)
            block_dict["stone"]=1
            block_dict["dirt"]=2
            block_dict["log"]=3
            #print(grid[-1]) #to print the block in front
            grid_binary = [block_dict[x] for x in grid]
            obs = np.reshape(grid_binary, (2,OBS_SIZE))
            # Rotate observation with orientation of agent
            break

    return obs


def prepare_batch(replay_buffer):
    """
    Randomly sample batch from replay buffer and prepare tensors

    Args:
        replay_buffer (list): obs, action, next_obs, reward, done tuples

    Returns:
        obs (tensor): float tensor of size (BATCH_SIZE x obs_size
        action (tensor): long tensor of size (BATCH_SIZE)
        next_obs (tensor): float tensor of size (BATCH_SIZE x obs_size)
        reward (tensor): float tensor of size (BATCH_SIZE)
        done (tensor): float tensor of size (BATCH_SIZE)
    """
    batch_data = random.sample(replay_buffer, BATCH_SIZE)
    obs = torch.tensor(np.array([x[0] for x in batch_data]), dtype=torch.float)
    action = torch.tensor(np.array([x[1] for x in batch_data]), dtype=torch.long)
    next_obs = torch.tensor(np.array([x[2] for x in batch_data]), dtype=torch.float)
    reward = torch.tensor(np.array([x[3] for x in batch_data]), dtype=torch.float)
    done = torch.tensor(np.array([x[4] for x in batch_data]), dtype=torch.float)

    return obs, action, next_obs, reward, done


def learn(batch, optim, q_network, target_network):
    """
    Update Q-Network according to DQN Loss function

    Args:
        batch (tuple): tuple of obs, action, next_obs, reward, and done tensors
        optim (Adam): Q-Network optimizer
        q_network (QNetwork): Q-Network
        target_network (QNetwork): Target Q-Network
    """
    obs, action, next_obs, reward, done = batch

    optim.zero_grad()
    values = q_network(obs).gather(1, action.unsqueeze(-1)).squeeze(-1)
    target = torch.max(target_network(next_obs), 1)[0]
    target = reward + GAMMA * target * (1 - done)
    loss = torch.mean((target - values) ** 2)
    loss.backward()
    optim.step()

    return loss.item()

def get_block_front(world_state):
    if world_state.number_of_observations_since_last_state > 0:
        msg = world_state.observations[-1].text
        observations = json.loads(msg)
        grid = observations['floorAll']
        return grid[-3]
    return "Problem in get_block_front"

def get_inv_observation(world_state):
    """
    Use the agent observation API to view the hotbar
    length 10 array

    Args
        world_state: <object> current agent world state

    Returns
        observation: <array> Strings
    """
    inv_obs = []  # create an empty string array to store the "hotbar"
    for i in range(9):
        inv_obs.append("") # fill it with empty strings

    while world_state.is_mission_running:
        time.sleep(0.1)
        world_state = agent_host.getWorldState()
        if len(world_state.errors) > 0:
            raise AssertionError('Could not load grid.')

        if world_state.number_of_observations_since_last_state > 0:
            # First we get the json from the observation API
            msg = world_state.observations[-1].text
            obs = json.loads(msg)

            for item in obs[u'inventory']:
                name = item['type']
                i = int(item['index'])
                inv_obs[i] = name
            break

    return inv_obs

def log_returns(episodes, returns, times):
    # box = np.ones(10) / 10
    # returns_smooth = np.convolve(returns, box, mode='same')
    plt.clf()
    plt.plot(episodes, returns)
    plt.title('Secret Tunnel')
    plt.ylabel('Reward')
    plt.xlabel('Iteration')
    plt.savefig('returns_rewards.png')

    plt.clf()
    plt.plot(episodes, times)
    plt.title('Secret Tunnel')
    plt.ylabel('times (seconds)')
    plt.xlabel('Iteration')
    plt.savefig('returns_times.png')

def train(agent_host):
    # Init networks
    q_network = QNetwork(( 2,OBS_SIZE), len(ACTION_DICT))
    target_network = QNetwork((2,OBS_SIZE), len(ACTION_DICT))
    target_network.load_state_dict(q_network.state_dict())

    # Init optimizer
    optim = torch.optim.Adam(q_network.parameters(), lr=LEARNING_RATE)

    # Init replay buffer
    replay_buffer = deque(maxlen=REPLAY_BUFFER_SIZE)

    # Init vars
    global_step = 0
    num_episode = 0
    epsilon = 1
    start_time = time.time()
    returns = []
    times = []
    episodes = []

    # Begin main loop
    loop = tqdm(total=MAX_GLOBAL_STEPS, position=0, leave=False)
    while global_step < MAX_GLOBAL_STEPS:
        episode_start_time = time.time()
        episode_step = 0
        episode_return = 0
        episode_loss = 0
        done = False

        # Setup Malmo
        agent_host = init_malmo(agent_host, num_episode) # tunnel length is dependent on num_episode
        world_state = agent_host.getWorldState()
        while not world_state.has_mission_begun:
            time.sleep(0.1)
            world_state = agent_host.getWorldState()
            for error in world_state.errors:
                print("\nError:",error.text)
        obs = get_observation(world_state)

        # Run episode
        while world_state.is_mission_running:
            # Get action
            allow_break_action = obs[1,1] !=0
            action_idx = get_action(obs, q_network, epsilon, allow_break_action)
            commands = ACTION_DICT[action_idx]#switch tools
            agent_host.sendCommand("move 0")
            # Take step
            for command in commands:
                agent_host.sendCommand(command)
            while allow_break_action:
                agent_host.sendCommand("attack 1")
                obs1 = get_observation(world_state)
                allow_break_action = obs1[1,1] !=0
            agent_host.sendCommand("attack 0")
            agent_host.sendCommand("move 1")
            # If your agent isn't registering reward you may need to increase this
            time.sleep(0.3)

            # We have to manually calculate terminal state to give malmo time to register the end of the mission
            # If you see "commands connection is not open. Is the mission running?" you may need to increase this
            episode_step += 1
            if (episode_step >= MAX_EPISODE_STEPS):
                done = True
                time.sleep(2)

            # Get next observation
            world_state = agent_host.getWorldState()
            for error in world_state.errors:
                print("Error:", error.text)
            next_obs = get_observation(world_state)

            reward = 0
            #Get reward
            for r in world_state.rewards:
                tunnel_length = num_episode*TUNNEL_LEN_DELTA + TUNNEL_LEN_START
                reward =int((tunnel_length)/(r.getValue())*10000)

            episode_return += reward

            # Store step in replay buffer
            replay_buffer.append((obs, action_idx, next_obs, reward, done))
            obs = next_obs

            # Learn
            global_step += 1
            if global_step > START_TRAINING and global_step % LEARN_FREQUENCY == 0:
                batch = prepare_batch(replay_buffer)
                loss = learn(batch, optim, q_network, target_network)
                episode_loss += loss

                if epsilon > MIN_EPSILON:
                    epsilon *= EPSILON_DECAY

                if global_step % TARGET_UPDATE == 0:
                    target_network.load_state_dict(q_network.state_dict())
        episode_time = (time.time() - episode_start_time)
        num_episode += 1
        returns.append(episode_return)
        episodes.append(num_episode)
        times.append(episode_time)
        avg_return = sum(returns[-min(len(returns), 10):]) / min(len(returns), 10)
        loop.update(episode_step)
        loop.set_description('Episode: {} Steps: {} Time: {:.2f} Loss: {:.2f} Last Return: {:.2f} Avg Return: {:.2f}'.format(
            num_episode, global_step, (time.time() - start_time) / 60, episode_loss, episode_return, avg_return))

        if num_episode > 0:
            log_returns(episodes, returns, times)
            print()


if __name__ == '__main__':
    # Create default Malmo objects:
    agent_host = MalmoPython.AgentHost()
    try:
        agent_host.parse( sys.argv )
    except RuntimeError as e:
        print('ERROR:', e)
        print(agent_host.getUsage())
        exit(1)
    if agent_host.receivedArgument("help"):
        print(agent_host.getUsage())
        exit(0)

    train(agent_host)
