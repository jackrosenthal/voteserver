import io
import socketserver
import yaml
import readline
import sys
from threading import Thread, RLock, current_thread
from datetime import datetime
from queue import Queue
from collections import defaultdict, OrderedDict

vote_q = Queue()
login_q = Queue()

open_polls = []
config_fn = sys.argv[2] if len(sys.argv) > 2 else 'vote.yaml'

with open(config_fn) as f:
    config = yaml.load(f)

logging_lock = RLock()
def log(*args, date=True, **kwargs):
    with logging_lock:
        print('\r        \r' + (str(datetime.now()) if date else ''), end='')
        print(*args, **kwargs)
        sys.stdout.flush()
        readline.redisplay()
        sys.stderr.flush()

class ShutdownMessage:
    pass

class LoginProcessor(Thread):
    def __init__(self):
        self.next_id = 0
        self.clients = {}
        super().__init__()

    def run(self):
        while True:
            th = login_q.get()
            self.clients[self.next_id] = th
            log("{} ({}) registered to vote!".format(self.next_id, th.name))
            for poll in open_polls:
                th.poll_q.put(poll)
            self.next_id += 1
            login_q.task_done()

class VoteProcessor(Thread):
    def __init__(self):
        self.votes = defaultdict(lambda :defaultdict(int))
        super().__init__()

    def run(self):
        while True:
            vote = vote_q.get()
            self.votes[vote[0]][vote[1]] += 1
            vote_q.task_done()

class ClientConnectionHandler(socketserver.StreamRequestHandler):
    def message(self, msg):
        self.clear_and_banner()
        print('\n' + msg, file=self.out)

    def handle(self):
        self.out = io.TextIOWrapper(self.wfile, line_buffering=True)
        self.th = current_thread()
        self.th.poll_q = Queue()
        self.login()
        while True:
            self.message('Waiting for polls to open...')
            poll = self.th.poll_q.get()
            if poll is ShutdownMessage:
                self.message('Polling is over. Thanks for voting!')
                self.th.poll_q.task_done()
                break
            if poll not in open_polls:
                continue
            self.handle_poll(poll)

    def handle_poll(self, poll_name):
        self.clear_and_banner()
        poll = config['polls'][poll_name]
        print(file=self.out)
        if 'question' in poll.keys():
            print(poll['question'], file=self.out)
            print(file=self.out)
        if 'title' in poll.keys():
            print('Please select your vote for "{}".'.format(poll['title']), file=self.out)
            print(file=self.out)
        if 'note' in poll.keys():
            print(poll['note'], file=self.out)
            print(file=self.out)
        if 'yesno' in poll.keys() and poll['yesno']:
            while True:
                print('Type YES, NO, or ABSTAIN: ', file=self.out, end='')
                self.out.flush()
                response = self.rfile.readline().decode('utf-8').strip()
                if response in {'YES', 'NO', 'ABSTAIN'}:
                    break
            vote_q.put((poll_name, response))
        elif 'options' in poll.keys():
            writein = 'writein' in poll.keys() and poll['writein']
            # if a poll is both ranked and multichoiced, it is assumed that ranked is desired
            ranked = 'ranked' in poll.keys() and poll['ranked']
            multichoice = 'multichoice' in poll.keys() and poll['multichoice'] # aka approval voting
            for i, opti in enumerate(poll['options']):
                print("    {}) {}".format(i + 1, opti), file=self.out)
            print(file=self.out)
            if ranked:
                print("You may rank multiple options seperated by commas. (e.g. 1,3,5)", file=self.out)
            elif multichoice:
                print("You may select multiple options separated by commas. (e.g., 1,3,5)", file=self.out)
            while True:
                print('Type the number{} you want{}, or ABSTAIN: '.format(
                    '(s)' if ranked or multichoice else '',
                    ', write-in a response' if writein else ''), file=self.out, end='')
                self.out.flush()
                line = self.rfile.readline().decode('utf-8').strip()
                if not line:
                    continue
                if ranked:
                    # splits the line by commas, then strips, then removes empty
                    # strings, then removes duplicates (keeping leftmost occurence)
                    aline = list(OrderedDict.fromkeys(filter(lambda x: x != '',
                                                             [r.strip() for r in line.split(',')])))
                elif multichoice:
                    aline = list(set(r.strip() for r in line.split(',')))
                else:
                    aline = [line]
                for i in range(len(aline)):
                    try:
                        aline[i] = int(aline[i])
                        if 1 <= aline[i] <= len(poll['options']):
                            aline[i] = poll['options'][aline[i] - 1]
                        else:
                            break
                    except ValueError:
                        if not (writein or aline[i] == 'ABSTAIN'):
                            break
                        if aline[i] == 'ABSTAIN' and len(aline) > 1:
                            break
                else:
                    break
            if ranked:
                vote_q.put((poll_name, tuple(aline)))
            else:
                for r in aline:
                    vote_q.put((poll_name, r))

    def login(self):
        self.clear_and_banner()
        if 'welcome_msg' in config.keys():
            print('\n' + config['welcome_msg'] + '\n', file=self.out)
        print('Please enter your name, it will be used to catch voting discrepancies.', file=self.out)
        print('Your name will NOT be associated with your votes.\n', file=self.out)
        print('Your full name: ', end='', file=self.out)
        self.out.flush()
        self.th.name = self.rfile.readline().decode('utf-8').strip()
        login_q.put(self.th)

    def clear_and_banner(self):
        print("\x1b[2J\x1b[H", end='', file=self.out)
        if 'banner' in config.keys():
            print(config['banner'], end='', file=self.out)
        self.out.flush()

commands = {}
def command(f):
    commands[f.__name__] = f
    return f

@command
def exit():
    for th in lp.clients.values():
        th.poll_q.put(ShutdownMessage)
    for th in lp.clients.values():
        th.join()
    server.shutdown()
    server.server_close()
    sys.exit()

@command
def kick(client_id):
    try:
        th = lp.clients.pop(int(client_id))
        log('Kicking {} ({})...'.format(client_id, th.name))
        th.poll_q.put(ShutdownMessage)
        th.join()
    except ValueError:
        log('kick: client id must be an integer', date=False)
    except KeyError:
        log('kick: client id unknown', date=False)

@command
def who():
    for client_id, th in lp.clients.items():
        log('{} ({})'.format(client_id, th.name), date=False)

@command
def vote(name):
    global open_polls
    if name not in config['polls'].keys():
        log("Unknown poll '{}'".format(name), date=False)
        return
    open_polls.append(name)
    for th in lp.clients.values():
        th.poll_q.put(name)

@command
def close(name):
    global open_polls
    open_polls = [p for p in open_polls if p != name]

_list = list
@command
def list():
    for k in config['polls'].keys():
        log(k, date=False)
list = _list

@command
def results(name):
    if name not in config['polls'].keys():
        log("Unknown poll '{}'".format(name), date=False)
        return
    if 'ranked' in config['polls'][name] and config['polls'][name]['ranked']:
        # maps a candidates to a list in which the 1st element is the count of
        # the candidate's 1st-place votes and the remaining elements are tuples
        # where the 1st element is a count of a sequence and the 2nd element is
        # the sequence itself
        candidates = defaultdict(lambda: [0])

        for seq, count in vp.votes[name].items():
            candidates[seq[0]][0] += count
            candidates[seq[0]].append((count, seq[1:]))

        standings = []  # reverse order: last place to first place

        while candidates:
            # sort by count of first-place votes
            candidates = OrderedDict(sorted(candidates.items(), key=lambda x: x[1][0]))

            # eliminate candidate with least amount of first-place votes
            loser = candidates.popitem(last=False)
            standings.append((loser,))  # tuple so candidates can tie

            # eliminate candidates whose score is the same as the loser's (tie)
            while next((v[0] for v in candidates.values()), None) == loser[1][0]:
                standings[-1] += (candidates.popitem(last=False),)

            # distribute each losing candidate's vote to the vote's next best candidate
            for loser in standings[-1]:
                for count, seq in loser[1][1:]:
                    alt = next((seq[i:] for i in range(len(seq)) if seq[i] in candidates.keys()), None)
                    if alt:
                        candidates[alt[0]][0] += count
                        candidates[alt[0]].append((count, alt[1:]))

        for rank, cands in enumerate(standings[::-1]):
            for cand in cands:
                log("{}. {}".format(rank + 1, cand[0]), date=False)
        return
    total = sum(votes for opt, votes in vp.votes[name].items() if opt != 'ABSTAIN')
    for opt, votes in vp.votes[name].items():
        log("{} - {} votes ({}%)".format(opt, votes, votes/total * 100 if opt != 'ABSTAIN' else 0.0), date=False)

@command
def options(name):
    if name not in config['polls'].keys():
        log("Unknown poll '{}'".format(name), date=False)
        return
    if 'options' not in config['polls'][name].keys():
        log("Poll has no options".format(name), date=False)
        return
    for i, opti in enumerate(config['polls'][name]['options']):
        log(i + 1, opti)

@command
def rmopt(name, index):
    if name not in config['polls'].keys():
        log("Unknown poll '{}'".format(name), date=False)
        return
    if 'options' not in config['polls'][name].keys():
        log("Poll has no options".format(name), date=False)
        return
    index = int(index)
    if not 1 <= index <= len(config['polls'][name]['options']):
        log("Option not in range", date=False)
        return
    del config['polls'][name]['options'][index - 1]

@command
def revote(name):
    close(name)
    del vp.votes[name]
    vote(name)

if __name__ == '__main__':
    lp = LoginProcessor()
    lp.daemon = True
    lp.start()

    vp = VoteProcessor()
    vp.daemon = True
    vp.start()

    server = socketserver.ThreadingTCPServer(('0.0.0.0', int(sys.argv[1])), ClientConnectionHandler)
    server_th = Thread(target=server.serve_forever)
    server_th.daemon = True
    server_th.start()

    while True:
        cmd = input('> ').split()
        if cmd and cmd[0] in commands:
            try:
                commands[cmd[0]](*cmd[1:])
            except Exception as e:
                log("Error processing directive: {}".format(e), date=False)

