import enum
import json
import random
import socket
import traceback
from collections import deque
from contextlib import suppress
from itertools import islice
from string import ascii_uppercase
from typing import Collection, Dict

import gevent.server


def get_random_id(k=5):
    return ''.join(random.choices(ascii_uppercase, k=k))


class GameState(enum.Enum):
    LOBBY = 'LOBBY'
    PREPARATION = 'PREPARATION'
    MAIN = 'MAIN'
    ENDED = 'ENDED'


class Player:
    def __init__(self, client, name=""):
        while True:
            self.id = get_random_id()
            if self.id not in players: break
        self.name = name
        self.client = client
        self.ready = False
        self.score = 0

    def reset(self):
        self.ready = False
        self.score = 0

    def __repr__(self):
        return f'Player({self.id}, {self.name})'


game_state = GameState.LOBBY
clients = list()
players = deque()
questions = list()
answers = list()


def client_connected(client: socket.socket, address):
    clients.append(client)
    player = Player(client)
    print(f'new connection from {address}')
    while True:
        try:
            data = recv_json(client)
        except ConnectionError:
            break
        except ValueError:
            traceback.print_exc()
            continue
        print(f'{address} sent "{data}"')
        try:
            callback = globals()['handle_' + data['op']]
        except KeyError as e:
            print(e)
        else:
            try:
                callback(client, player, data)
            except Exception:
                traceback.print_exc()
    with suppress(ValueError):
        players.remove(player)
    if not players:
        reset_game()
    clients.remove(client)
    print(f"client {address} disconnected")


def handle_Join(client, player, data):
    if game_state != GameState.LOBBY:
        return
    player.reset()
    player.name = data['username']
    if player not in players:
        players.append(player)
    send_json(client, {
        'op': 'Joined',
        'player-id': player.id
    })
    msg = {
        'op': 'LobbyUpdated',
        'players': [
            {'id': plr.id, 'name': plr.name, 'ready': plr.ready}
            for plr in players
        ]
    }
    broadcast(players, msg)


def handle_SetReady(client, player, data):
    print(f"Player {player} is ready")
    player.ready = data['ready']
    msg = {
        'op': 'LobbyUpdated',
        'players': [
            {'id': plr.id, 'name': plr.name, 'ready': plr.ready}
            for plr in players
        ]
    }
    broadcast(players, msg)
    if len(players) >= 3 and all(p.ready for p in players):
        global game_state
        game_state = GameState.PREPARATION
        broadcast(players, {
            "op": "PreparationStarted",
            "players": [
                {'id': plr.id, 'name': plr.name, 'ready': plr.ready}
                for plr in players
            ],
            "#questions": 2,
            "#answers": 2 * (len(players) - 1)
        })


def handle_AddQuestions(client, player, data):
    print(f'player {player} added questions')
    questions.extend(data['questions'])
    init_game()


def handle_AddAnswers(client, player, data):
    print(f'player {player} added answers')
    answers.extend(data['answers'])
    init_game()


def reset_game():
    global game_state
    game_state = GameState.LOBBY
    questions.clear()
    answers.clear()
    players.clear()


def init_game():
    min_questions = 2 * len(players)
    min_answers = 2 * len(players) * (len(players) - 1)
    if (len(questions) >= min_questions) and (len(answers) >= min_answers):
        print('starting main game')
        broadcast(players, {
            'op': 'MainStarted',
            "players": [
                {'id': plr.id, 'name': plr.name}
                for plr in players
            ]
        })
        global game_state
        game_state = GameState.MAIN
        random.shuffle(players)
        random.shuffle(questions)
        random.shuffle(answers)
        play_turn()


def handle_Vote(client, player, data):
    if player == players[0]:
        winner = next(plr for plr in players if plr.id == data['winner'])
        winner.score += 1
        broadcast(players, {
            'op': 'UpdateScore',
            'scores': [
                {'id': plr.id, 'name': plr.name, 'score': plr.score}
                for plr in players
            ],
            'winner': {'id': winner.id, 'name': winner.name}
        })
        if len(questions) > 0:
            play_turn()
        else:
            summarize_game()


def play_turn():
    question = questions.pop()
    current_player = players.pop()
    players.appendleft(current_player)
    for player in islice(players, 1, None):
        send_json(player.client, {
            'op': 'NewTurn',
            'your-turn': False,
            'current-player': {'id': current_player.id,
                               'name': current_player.name},
            'question': question,
            'answer': answers.pop()
        })
    send_json(current_player.client, {
        'op': 'NewTurn',
        'your-turn': True,
        'current-player': {'id': current_player.id,
                           'name': current_player.name},
        'question': question,
        'answer': None
    })


def summarize_game():
    global game_state
    game_state = GameState.ENDED
    ordered = sorted(players, key=lambda p: p.score, reverse=True)
    winner = ordered[0]
    broadcast(players, {
        'op': 'GameOver',
        'scores': [
            {'id': plr.id, 'name': plr.name, 'score': plr.score}
            for plr in ordered
        ],
        'winner': {'id': winner.id, 'name': winner.name, 'score': winner.score}
    })
    reset_game()



def broadcast(players: Collection[Player], msg: Dict):
    gevent.wait([
        gevent.spawn(send_json, plr.client, msg)
        for plr in players
    ])


def send_json(client, obj):
    data = json.dumps(obj).encode()
    client.sendall(len(data).to_bytes(4, 'big'))
    client.sendall(data)


def recv_json(client):
    size = b''.join(client.recv(1) for _ in range(4))
    size = int.from_bytes(size, 'big')
    if not size: raise ConnectionResetError
    buffer = b''
    while len(buffer) < size:
        buffer += client.recv(size - len(buffer))
    print(f'read {buffer}')
    return json.loads(buffer.decode())


ADDRESS = ('127.0.0.1', 7575)


if __name__ == '__main__':
    server = gevent.server.StreamServer(ADDRESS, client_connected)
    server.start()
    print(f'server started {server.server_host}:{server.server_port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()


