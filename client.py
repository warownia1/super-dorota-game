import contextlib
import json
import socket
from collections import deque
from functools import partial
from threading import Thread

from kivy.app import App
from kivy.config import Config
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.stacklayout import StackLayout
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

Config.set('graphics', 'width', '432')
Config.set('graphics', 'height', '768')

class ConnectionManager(Thread):
    def __init__(self):
        Thread.__init__(self)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._unprocessed = deque()
        self._listeners = list()

    def connect(self, address):
        self._socket.connect(address)

    def send(self, data):
        data = json.dumps(data).encode()
        self._socket.sendall(len(data).to_bytes(4, 'big'))
        self._socket.sendall(data)

    def recv(self):
        size = b''.join(self._socket.recv(1) for _ in range(4))
        size = int.from_bytes(size, 'big')
        buffer = b''
        while len(buffer) < size:
            buffer += self._socket.recv(size - len(buffer))
        return json.loads(buffer.decode())

    def add_listener(self, listener):
        self._listeners.append(listener)
        unprocessed = []
        while self._unprocessed:
            msg = self._unprocessed.popleft()
            if not listener(msg):
                unprocessed.append(msg)
        self._unprocessed.extend(unprocessed)

    def listener(self, listener):
        self.add_listener(listener)
        return listener

    def remove_listener(self, listener):
        self._listeners.remove(listener)

    def close(self):
        with contextlib.suppress(OSError):
            self._socket.shutdown(socket.SHUT_RDWR)
        self._socket.close()

    def run(self):
        while True:
            msg = self.recv()
            print(f'received {msg}')
            for listener in self._listeners:
                print(f'trying {listener}')
                if listener(msg):
                    print('message processed')
                    break
            else:
                print(f"Unprocessed message {msg['op']}")


client = ConnectionManager()
player_id = None
username = ''


class LoginScreen(Screen):
    def __init__(self):
        Screen.__init__(self, name='Login')
        box = BoxLayout(orientation='vertical')
        layout = GridLayout(size_hint=(1, 0.6))
        layout.cols = 2

        layout.add_widget(Label(
            text="name", font_size=36, size_hint=(0.4, 1)
        ))
        self.name_input = TextInput(
            font_size=36, multiline=False, halign='center', size_hint=(0.6, 1)
        )
        layout.add_widget(self.name_input)

        layout.add_widget(Label(
            text="server", font_size=36, size_hint=(0.4, 1)
        ))
        self.ip_input = TextInput(
            font_size=36, multiline=False, halign='center', size_hint=(0.6, 1)
        )
        layout.add_widget(self.ip_input)

        box.add_widget(layout)
        self.join_btn = Button(text="Join", font_size=42, size_hint=(1, 0.3))
        self.join_btn.bind(on_release=self.on_join)
        box.add_widget(self.join_btn)
        self.add_widget(box)

    def on_join(self, instance: Button):
        instance.disabled = True
        instance.text = "Connecting..."
        address = tuple(self.ip_input.text.split(':', 1))
        if len(address) == 1:
            address = (address[0] or '127.0.0.1', 7575)
        else:
            address = (address[0], int(address[1]))
        try:
            client.connect(address)
            global username
            username = self.name_input.text
            client.send({"op": "Join", "username": username})
            client.add_listener(self.login_handler)
            client.start()
        except Exception as e:
            popup = Popup(title="Server Error",
                          content=Label(text=str(e)),
                          auto_dismiss=True,
                          size_hint=(0.5, 0.5))
            popup.open()
            self.join_btn.text = "Join"
            self.join_btn.disabled = False

    def login_handler(self, msg):
        if msg.get('op') == 'Joined':
            global player_id
            player_id = msg.get("player-id")
            self.manager.current = "Lobby"
            return True


class LobbyScreen(Screen):
    def __init__(self, **kwargs):
        Screen.__init__(self, name="Lobby", **kwargs)
        client.add_listener(self.players_list_update_handler)
        client.add_listener(self.game_started_handler)

        _layout = BoxLayout(orientation='vertical')
        self.add_widget(_layout)

        self.players_stack = StackLayout(orientation='tb-lr', spacing=10)
        _layout.add_widget(self.players_stack)

        self.ready_btn = ToggleButton(text="Ready", font_size=42, size_hint=(1, 0.3))
        self.ready_btn.bind(state=self.on_ready)
        _layout.add_widget(self.ready_btn)

    def on_pre_enter(self, *args):
        client.add_listener(self.players_list_update_handler)
        client.add_listener(self.game_started_handler)

    def on_pre_leave(self, *args):
        client.remove_listener(self.players_list_update_handler)
        client.remove_listener(self.game_started_handler)

    def on_leave(self, *args):
        self.ready_btn.state = 'normal'

    def on_ready(self, instance, value):
        client.send({"op": 'SetReady', 'ready': value == 'down'})

    def players_list_update_handler(self, msg):
        if msg.get('op') == 'LobbyUpdated':
            self.players_stack.clear_widgets()
            for player in msg['players']:
                self.players_stack.add_widget(Label(
                    text=player['name'], font_size=24, size_hint=(0.5, 0.2),
                    color=(0, 0.7, 0, 1) if player['ready'] else (1, 1, 1, 1)
                ))
            return True

    def game_started_handler(self, msg):
        if msg.get('op') == 'PreparationStarted':
            prep_screen = self.manager.get_screen("Preparation")
            prep_screen.init(msg['#questions'], msg['#answers'])
            self.manager.current = "Preparation"
            return True


class PreparationScreen(Screen):
    def __init__(self, **kwargs):
        Screen.__init__(self, name="Preparation", **kwargs)
        _layout = BoxLayout(orientation='vertical')
        self._question_grid = BoxLayout(orientation='vertical', size_hint=(1, 0.2))
        _layout.add_widget(self._question_grid)
        self._answer_grid = GridLayout(cols=3, size_hint=(1, 0.4))
        _layout.add_widget(self._answer_grid)
        self.send_btn = Button(text="Send", font_size=36, size_hint=(1, 0.2))
        self.send_btn.bind(on_release=self.on_send)
        _layout.add_widget(self.send_btn)
        self.add_widget(_layout)
        self._questions = []
        self._answers = []

    def init(self, num_questions, num_answers):
        self._question_grid.clear_widgets()
        self._answer_grid.clear_widgets()
        self._questions = [
            TextInput(multiline=False, font_size=36)
            for _ in range(num_questions)
        ]
        for widget in self._questions:
            self._question_grid.add_widget(widget)
        self._answers = [
            TextInput(multiline=False, font_size=28)
            for _ in range(num_answers)
        ]
        for widget in self._answers:
            self._answer_grid.add_widget(widget)

    def on_pre_enter(self, *args):
        client.add_listener(self.game_started_handler)

    def on_pre_leave(self, *args):
        client.remove_listener(self.game_started_handler)

    def on_leave(self, *args):
        self.send_btn.disabled = False
        self.send_btn.text = 'Send'

    def on_send(self, instance: Button):
        questions = [widget.text for widget in self._questions]
        answers = [widget.text for widget in self._answers]
        if all(questions) and all(answers):
            instance.disabled = True
            instance.text = "Waiting for others..."
            client.send({"op": "AddQuestions", "questions": questions})
            client.send({"op": "AddAnswers", "answers": answers})

    def game_started_handler(self, msg):
        if msg.get('op') == 'MainStarted':
            players = [(plr['id'], plr['name']) for plr in msg['players']
                       if plr['id'] != player_id]
            next_screen = GameScreen(players)
            self.manager.add_widget(next_screen)
            self.manager.current = next_screen.name


class GameScreen(Screen):
    def __init__(self, players, name="Game"):
        Screen.__init__(self, name=name)
        _layout = BoxLayout(orientation='vertical')
        self._question = Label(text='question', markup=True, font_size=36)
        _layout.add_widget(self._question)
        self._answer = Label(text='', font_size=42)
        _layout.add_widget(self._answer)
        self._vote_buttons = StackLayout(orientation='tb-lr', spacing=10)
        for player_id, player_name in players:
            btn = Button(text=f"{player_id}:{player_name}",
                         size_hint=(0.3, 0.2), disabled=True)
            btn.bind(on_release=partial(self.on_vote, player_id=player_id))
            self._vote_buttons.add_widget(btn)
        _layout.add_widget(self._vote_buttons)
        self.add_widget(_layout)

    def on_pre_enter(self, *args):
        client.add_listener(self.new_turn_handler)
        client.add_listener(self.game_over_handler)

    def on_pre_leave(self, *args):
        client.remove_listener(self.new_turn_handler)
        client.remove_listener(self.game_over_handler)

    def on_leave(self, *args):
        self.manager.remove_widget(self)

    def on_vote(self, instance: Button, *, player_id):
        for button in self._vote_buttons.children:
            button.disabled = True
        client.send({'op': 'Vote', 'winner': player_id})

    def new_turn_handler(self, msg):
        if msg.get('op') == 'NewTurn':
            if msg['your-turn']:
                self._question.color = (1, 1, 1)
                for button in self._vote_buttons.children:
                    button.disabled = False
            else:
                self._question.color = (0.5, 0.5, 0.5)
            self._question.text = msg['question']
            self._answer.text = msg['answer'] or ''
            return True

    def game_over_handler(self, msg):
        if msg.get('op') == 'GameOver':
            ladderboard = msg['scores']
            next_screen = SummaryScreen(ladderboard)
            self.manager.add_widget(next_screen)
            self.manager.current = next_screen.name
            return True


class SummaryScreen(Screen):
    def __init__(self, ladderboard):
        Screen.__init__(self, name='Summary')
        _layout = BoxLayout(orientation='vertical')
        _scores = GridLayout(cols=2, size_hint=(1, 0.75))
        for entry in ladderboard[:5]:
            _scores.add_widget(Label(text=entry['name'], font_size=36))
            _scores.add_widget(Label(text=str(entry['score']), font_size=42))
        _layout.add_widget(_scores)
        _again_btn = Button(text='Play Again', size_hint=(1, 0.25), font_size=42)
        _again_btn.bind(on_release=self.on_play_again)
        _layout.add_widget(_again_btn)
        self.add_widget(_layout)

    def on_leave(self, *args):
        self.manager.remove_widget(self)

    def on_play_again(self, instance):
        self.manager.current = "Lobby"
        client.send({"op": "Join", "username": username})



class DorotaApp(App):
    def __init__(self):
        App.__init__(self)
        self.sm = ScreenManager()

    def build(self):
        self.sm.add_widget(LoginScreen())
        self.sm.add_widget(LobbyScreen())
        self.sm.add_widget(PreparationScreen())
        return self.sm


if __name__ == '__main__':
    try:
        app = DorotaApp()
        app.run()
    finally:
        client.close()
