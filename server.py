import os, json, secrets, hashlib, datetime, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from bson import ObjectId
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from io import BytesIO
import qrcode

BASE = os.path.dirname(os.path.abspath(__file__))
# Production configuration is supplied through environment variables (Render/MongoDB Atlas).
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://127.0.0.1:27017/')
DB_NAME = os.environ.get('MONGO_DB', 'booyah_arena')
ADMIN_USER = os.environ.get('BOOYAH_ADMIN_USER', 'Munivel@9866')
ADMIN_PASS = os.environ.get('BOOYAH_ADMIN_PASS', 'MuNiVel@1143')
UPI_ID = os.environ.get('BOOYAH_UPI_ID', '9940879866@nyes')
UPI_NAME = os.environ.get('BOOYAH_UPI_NAME', 'BOOYAH ARENA')

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]
players = db.players
deposits = db.deposits
matches = db.matches
withdrawals = db.withdrawals
rooms = db.rooms
admin_actions = db.admin_actions
SESSIONS = {}

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

def next_match_time(tournament):
    # Match slots use the computer's local timezone, matching the player UI.
    local = datetime.datetime.now().astimezone()
    day = local.date()
    # BR ₹50 starts every 30 minutes. Both Lone Wolf tiers start every 20 minutes.
    interval = 30 if tournament == 'BR ₹50 Room' else 20
    if tournament not in ('BR ₹50 Room', 'Lone Wolf ₹50', 'Lone Wolf ₹100'):
        raise ValueError('Unknown tournament')
    minute = (local.minute // interval) * interval
    candidate = datetime.datetime.combine(day, datetime.time(local.hour, minute), tzinfo=local.tzinfo)
    if candidate <= local:
        candidate += datetime.timedelta(minutes=interval)
    # Daily operating window: 1:00 PM through the final slot before 9:00 PM.
    if candidate.hour < 12:
        candidate = datetime.datetime.combine(day, datetime.time(13, 0), tzinfo=local.tzinfo)
    elif candidate.hour >= 20:
        candidate = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(12, 0), tzinfo=local.tzinfo)
    return candidate.isoformat(timespec='seconds')

def parse_iso(value):
    try:
        return datetime.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except Exception:
        return None

def match_capacity(tournament):
    return {'BR ₹50 Room':48,'Lone Wolf ₹50':2,'Lone Wolf ₹100':2}.get(tournament,48)

def live_match_status(row):
    status=row.get('status','Upcoming')
    if status in ('Completed','Won','Lost','Cancelled'):
        return status
    start=parse_iso(row.get('scheduled_at'))
    if not start:
        return status
    now_dt=datetime.datetime.now(datetime.timezone.utc)
    start_utc=start.astimezone(datetime.timezone.utc) if start.tzinfo else start.replace(tzinfo=datetime.timezone.utc)
    duration = 30 if row.get('tournament') == 'BR ₹50 Room' else 20
    if start_utc <= now_dt < start_utc + datetime.timedelta(minutes=duration):
        return 'Live'
    if now_dt >= start_utc + datetime.timedelta(minutes=duration):
        return 'Completed' if status == 'Upcoming' else status
    return 'Upcoming'

def ph(password):
    return hashlib.sha256(password.encode()).hexdigest()

def oid(value):
    try: return ObjectId(str(value))
    except Exception: return None

def serialize(value):
    if isinstance(value, ObjectId): return str(value)
    if isinstance(value, dict): return {k: serialize(v) for k,v in value.items() if k != '_id'} | ({'id': str(value['_id'])} if '_id' in value else {})
    if isinstance(value, list): return [serialize(v) for v in value]
    return value

def json_send(h, code, obj):
    raw = json.dumps(serialize(obj)).encode()
    h.send_response(code)
    h.send_header('Content-Type', 'application/json')
    h.send_header('Content-Length', str(len(raw)))
    h.send_header('Access-Control-Allow-Origin', '*')
    h.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
    h.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    h.end_headers(); h.wfile.write(raw)

def read_json(h):
    n = int(h.headers.get('Content-Length','0'))
    return json.loads(h.rfile.read(n) or '{}')

def auth(h, admin=False):
    token = h.headers.get('Authorization','').replace('Bearer ','').strip()
    session = SESSIONS.get(token)
    if not session or session.get('admin') != admin: return None
    if not admin:
        try:
            r = players.find_one({'_id': ObjectId(session['player_id'])}, {'blocked':1})
            if not r or r.get('blocked', False):
                SESSIONS.pop(token, None)
                return None
        except Exception:
            return None
    return session

def player_obj(r):
    return {'id': str(r['_id']), 'username': r['username'], 'name': r['name'], 'email': r['email'], 'uid': r['uid'], 'points': r.get('points',0), 'blocked': bool(r.get('blocked', False)), 'created': r['created_at']}

def init_indexes():
    players.create_index([('username', ASCENDING)], unique=True)
    players.create_index([('email', ASCENDING)], unique=True)
    players.create_index([('uid', ASCENDING)], unique=True)
    deposits.create_index([('player_id', ASCENDING)])
    matches.create_index([('player_id', ASCENDING)])
    withdrawals.create_index([('player_id', ASCENDING)])
    try:
        rooms.drop_index('tournament_1')
    except Exception:
        pass
    rooms.create_index([('tournament', ASCENDING), ('scheduled_at', ASCENDING)], unique=True)
    # Verify the server is reachable at startup.
    client.admin.command('ping')

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): print('%s - %s' % (self.address_string(), fmt % args))
    def do_OPTIONS(self):
        self.send_response(204); self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Access-Control-Allow-Headers','Content-Type, Authorization'); self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS'); self.end_headers()
    def do_GET(self):
        p = urlparse(self.path).path
        if p == '/api/health':
            try:
                client.admin.command('ping')
                return json_send(self,200,{'ok':True,'database':'connected'})
            except Exception as e:
                return json_send(self,503,{'ok':False,'database':'unavailable'})
        if p.startswith('/api/'): return self.api_get(p)
        if p == '/': p = '/index.html'
        fp = os.path.join(BASE, p.lstrip('/'))
        if not os.path.isfile(fp): return self.send_error(404)
        ext = os.path.splitext(fp)[1]
        typ = {'.html':'text/html; charset=utf-8','.css':'text/css','.js':'application/javascript','.json':'application/json','.png':'image/png','.jpg':'image/jpeg','.ico':'image/x-icon'}.get(ext,'application/octet-stream')
        data = open(fp,'rb').read(); self.send_response(200); self.send_header('Content-Type',typ); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        p = urlparse(self.path).path
        try: d = read_json(self)
        except Exception: return json_send(self,400,{'error':'Invalid JSON'})
        if p.startswith('/api/'): return self.api_post(p,d)
        self.send_error(404)

    def api_get(self,p):
        s = auth(self)
        if p == '/api/upi-qr':
            if not s: return json_send(self,401,{'error':'Login required'})
            raw_amount = parse_qs(urlparse(self.path).query).get('amount',[''])[0]
            try: amount = int(raw_amount)
            except Exception: amount = 0
            if amount <= 0: return json_send(self,400,{'error':'Invalid payment amount'})
            upi_uri = f'upi://pay?pa={quote(UPI_ID)}&pn={quote(UPI_NAME)}&am={amount:.2f}&cu=INR'
            img = qrcode.make(upi_uri)
            buf = BytesIO(); img.save(buf, format='PNG'); data = buf.getvalue()
            self.send_response(200); self.send_header('Content-Type','image/png'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p == '/api/upi-details':
            if not s: return json_send(self,401,{'error':'Login required'})
            raw_amount = parse_qs(urlparse(self.path).query).get('amount',[''])[0]
            try: amount = int(raw_amount)
            except Exception: amount = 0
            if amount <= 0: return json_send(self,400,{'error':'Invalid payment amount'})
            return json_send(self,200,{'upi_id':UPI_ID,'name':UPI_NAME,'amount':amount,'upi_uri':f'upi://pay?pa={quote(UPI_ID)}&pn={quote(UPI_NAME)}&am={amount:.2f}&cu=INR'})
        if p == '/api/me':
            if not s: return json_send(self,401,{'error':'Login required'})
            r = players.find_one({'_id': ObjectId(s['player_id'])}); return json_send(self,200,{'player':player_obj(r)})
        if p == '/api/matches':
            if not s: return json_send(self,401,{'error':'Login required'})
            rows = list(matches.find({'player_id': ObjectId(s['player_id'])}).sort('scheduled_at',1))
            for row in rows: row['display_status'] = live_match_status(row)
            return json_send(self,200,{'matches':rows})
        if p == '/api/available-matches':
            if not s: return json_send(self,401,{'error':'Login required'})
            tournaments=['BR ₹50 Room','Lone Wolf ₹50','Lone Wolf ₹100']
            now_local=datetime.datetime.now().astimezone()
            out=[]
            for t in tournaments:
                interval=30 if t=='BR ₹50 Room' else 20
                local=now_local
                minute=(local.minute // interval)*interval
                candidate=datetime.datetime.combine(local.date(),datetime.time(local.hour,minute),tzinfo=local.tzinfo)
                if candidate <= local:
                    candidate += datetime.timedelta(minutes=interval)
                if candidate.hour < 12:
                    candidate=datetime.datetime.combine(candidate.date(),datetime.time(12,0),tzinfo=local.tzinfo)
                elif candidate.hour >= 20:
                    candidate=datetime.datetime.combine(candidate.date()+datetime.timedelta(days=1),datetime.time(12,0),tzinfo=local.tzinfo)
                for _ in range(4):
                    scheduled=candidate.isoformat(timespec='seconds')
                    joined=matches.count_documents({'tournament':t,'scheduled_at':scheduled})
                    mine=matches.find_one({'player_id':ObjectId(s['player_id']),'tournament':t,'scheduled_at':scheduled},{'_id':1}) is not None
                    if t=='BR ₹50 Room':
                        prize='1st ₹1,000 · ₹12/kill'
                    elif t=='Lone Wolf ₹50':
                        prize='Winner ₹80'
                    else:
                        prize='Winner ₹150'
                    out.append({'tournament':t,'scheduled_at':scheduled,'entry_fee':50 if t!='Lone Wolf ₹100' else 100,'capacity':match_capacity(t),'joined':joined,'joined_by_player':mine,'prize':prize})
                    candidate += datetime.timedelta(minutes=interval)
                    if candidate.hour >= 21:
                        break
            out.sort(key=lambda x:x['scheduled_at'])
            return json_send(self,200,{'matches':out})
        if p == '/api/withdrawals':
            if not s: return json_send(self,401,{'error':'Login required'})
            rows = list(withdrawals.find({'player_id': ObjectId(s['player_id'])}).sort('_id',-1)); return json_send(self,200,{'withdrawals':rows})
        if p == '/api/deposits':
            if not s: return json_send(self,401,{'error':'Login required'})
            rows = list(deposits.find({'player_id': ObjectId(s['player_id'])}).sort('_id',-1)); return json_send(self,200,{'deposits':rows})
        if p == '/api/public-schedule':
            # Public schedule shows real registration counts, but never exposes room credentials.
            tournaments=['BR ₹50 Room','Lone Wolf ₹50','Lone Wolf ₹100']
            out=[]
            for t in tournaments:
                slot=next_match_time(t)
                joined=matches.count_documents({'tournament':t,'scheduled_at':slot})
                out.append({'tournament':t,'scheduled_at':slot,'joined':joined,'capacity':match_capacity(t)})
            return json_send(self,200,{'schedule':out})
        if p == '/api/rooms':
            if not s: return json_send(self,401,{'error':'Login required'})
            player_matches = list(matches.find({'player_id': ObjectId(s['player_id'])},{'tournament':1,'scheduled_at':1}))
            keys={(m.get('tournament'),m.get('scheduled_at')) for m in player_matches}
            all_rooms=list(rooms.find({}, {'_id':0,'tournament':1,'scheduled_at':1,'room_id':1,'password':1,'updated_at':1}).sort('scheduled_at',1))
            now_dt=datetime.datetime.now(datetime.timezone.utc)
            rows=[]
            for r in all_rooms:
                if (r.get('tournament'),r.get('scheduled_at')) not in keys: continue
                start=parse_iso(r.get('scheduled_at'))
                if not start: continue
                start_utc=start.astimezone(datetime.timezone.utc) if start.tzinfo else start.replace(tzinfo=datetime.timezone.utc)
                # Once the admin publishes a room, reveal it to players who joined
                # that exact match slot. This fixes the case where a room was published
                # but the player dashboard kept showing "not published yet" until the
                # five-minute window. The match-slot membership check above still keeps
                # room credentials private from players who did not join that slot.
                rows.append(r)
            return json_send(self,200,{'rooms':rows})
        if p == '/api/admin/data':
            if not auth(self,True): return json_send(self,401,{'error':'Admin login required'})
            ps = list(players.find({}, {'username':1,'name':1,'email':1,'uid':1,'points':1,'blocked':1,'created_at':1}).sort('_id',-1))
            dep = list(deposits.find({}).sort('_id',-1)); wd = list(withdrawals.find({}).sort('_id',-1)); mt = list(matches.find({}).sort('_id',-1))
            pmap = {str(x['_id']): x for x in players.find({})}
            for arr in (dep,wd,mt):
                for x in arr:
                    p = pmap.get(str(x.get('player_id')),{})
                    x['name'] = p.get('name',''); x['uid'] = p.get('uid',''); x['email'] = p.get('email','');
                    if x in mt: x['display_status'] = live_match_status(x)
            # Build a real timetable for the admin, not only slots that already have players.
            # This lets the organiser see upcoming 30-minute BR slots (and 20-minute Lone Wolf slots)
            # before anyone joins, then publish a room for the exact slot. Existing player records are
            # merged into those generated slots so missed/late registrations remain visible.
            tournaments=['BR ₹50 Room','Lone Wolf ₹50','Lone Wolf ₹100']
            now_local=datetime.datetime.now().astimezone()
            now_utc=datetime.datetime.now(datetime.timezone.utc)
            grouped={}

            def add_group(t, scheduled):
                key=(t,scheduled)
                if key not in grouped:
                    grouped[key]={'tournament':t,'scheduled_at':scheduled,'capacity':match_capacity(t),'players':[]}
                return grouped[key]

            # Generate the next 12 slots for each format. The first slot is the next slot at/after now.
            for t in tournaments:
                interval=30 if t=='BR ₹50 Room' else 20
                local=now_local
                minute=(local.minute // interval)*interval
                candidate=datetime.datetime.combine(local.date(),datetime.time(local.hour,minute),tzinfo=local.tzinfo)
                if candidate <= local:
                    candidate += datetime.timedelta(minutes=interval)
                if candidate.hour < 12:
                    candidate=datetime.datetime.combine(candidate.date(),datetime.time(12,0),tzinfo=local.tzinfo)
                elif candidate.hour >= 20:
                    candidate=datetime.datetime.combine(candidate.date()+datetime.timedelta(days=1),datetime.time(12,0),tzinfo=local.tzinfo)
                for _ in range(12):
                    add_group(t,candidate.isoformat(timespec='seconds'))
                    candidate += datetime.timedelta(minutes=interval)
                    if candidate.hour >= 20:
                        break

            # Merge every actual player match, including older slots, into the timetable.
            for x in mt:
                key=(x.get('tournament'),x.get('scheduled_at'))
                if not x.get('tournament') or not x.get('scheduled_at'):
                    continue
                g=add_group(x.get('tournament'),x.get('scheduled_at'))
                g['players'].append({'id':str(x.get('_id')),'name':x.get('name'),'uid':x.get('uid'),'squad':x.get('squad'),'status':live_match_status(x)})

            room_docs=list(rooms.find({}, {'_id':0,'tournament':1,'scheduled_at':1,'room_id':1,'password':1,'updated_at':1}))
            room_map={(r.get('tournament'),r.get('scheduled_at')):r for r in room_docs}
            groups=[]
            for g in grouped.values():
                start=parse_iso(g['scheduled_at'])
                if not start:
                    continue
                start_utc=start.astimezone(datetime.timezone.utc) if start.tzinfo else start.replace(tzinfo=datetime.timezone.utc)
                duration=datetime.timedelta(minutes=30 if g['tournament']=='BR ₹50 Room' else 20)
                if start_utc <= now_utc < start_utc+duration:
                    status='Live'
                elif start_utc > now_utc:
                    status='Upcoming'
                else:
                    status='Completed'
                g['joined']=len(g['players'])
                g['status']=status
                g['room']=room_map.get((g['tournament'],g['scheduled_at']))
                groups.append(g)

            # Upcoming slots first, with actual registered-player history still included.
            groups.sort(key=lambda x:x.get('scheduled_at') or '')
            return json_send(self,200,{'players':ps,'deposits':dep,'withdrawals':wd,'matches':mt,'match_groups':groups})
        return json_send(self,404,{'error':'Not found'})

    def api_post(self,p,d):
        try:
            if p == '/api/register':
                for k in ('username','name','email','uid','password'):
                    if not d.get(k): return json_send(self,400,{'error':'All fields are required'})
                if len(d['password']) < 6: return json_send(self,400,{'error':'Password must be at least 6 characters'})
                uid = str(d['uid']).strip()
                if not uid.isdigit() or len(uid) != 10: return json_send(self,400,{'error':'Free Fire UID must be exactly 10 digits'})
                doc = {'username':d['username'].strip().lower(),'name':d['name'].strip(),'email':d['email'].strip().lower(),'uid':uid,'password_hash':ph(d['password']),'points':0,'blocked':False,'created_at':now()}
                try: r = players.insert_one(doc)
                except DuplicateKeyError: return json_send(self,409,{'error':'Username, email or UID already exists'})
                doc['_id'] = r.inserted_id; tok = secrets.token_urlsafe(32); SESSIONS[tok]={'player_id':str(r.inserted_id),'admin':False}
                return json_send(self,200,{'token':tok,'player':player_obj(doc)})

            if p == '/api/login':
                ident = str(d.get('identity','')).strip()
                password = str(d.get('password',''))
                if not ident or not password:
                    return json_send(self,400,{'error':'Username/email and password are required'})
                # Usernames are stored lowercase, so use an exact indexed lookup.
                # This avoids a case-insensitive regex scan and makes login much faster.
                ident_value = ident.lower()
                r = players.find_one({'$or':[{'username':ident_value},{'email':ident_value}], 'password_hash':ph(password)})
                if not r: return json_send(self,401,{'error':'Invalid username/email or password'})
                if r.get('blocked', False): return json_send(self,403,{'error':'Your player account is blocked. Please contact the administrator.'})
                tok=secrets.token_urlsafe(32); SESSIONS[tok]={'player_id':str(r['_id']),'admin':False}; return json_send(self,200,{'token':tok,'player':player_obj(r)})
            if p == '/api/admin/login':
                if d.get('username') != ADMIN_USER or d.get('password') != ADMIN_PASS: return json_send(self,401,{'error':'Invalid admin credentials'})
                tok=secrets.token_urlsafe(32); SESSIONS[tok]={'admin':True}; return json_send(self,200,{'token':tok})
            if p == '/api/logout':
                token=self.headers.get('Authorization','').replace('Bearer ','').strip(); SESSIONS.pop(token,None); return json_send(self,200,{'ok':True})

            s=auth(self)
            if p == '/api/deposit' and s:
                amt=int(d.get('amount',0)); ref=d.get('reference','').strip()
                if amt<10 or not ref: return json_send(self,400,{'error':'Deposit amount must be at least ₹10 and transaction ID is required'})
                if deposits.find_one({'reference':ref}): return json_send(self,409,{'error':'This transaction ID has already been submitted'})
                deposits.insert_one({'player_id':ObjectId(s['player_id']),'amount':amt,'reference':ref,'status':'Pending','created_at':now(),'submitted_after_payment':True}); return json_send(self,200,{'ok':True})
            if p == '/api/match' and s:
                prices={'BR ₹50 Room':50,'Lone Wolf ₹50':50,'Lone Wolf ₹100':100}; t=d.get('tournament'); fee=prices.get(t); squad=d.get('squad','').strip()
                if not fee or not squad:return json_send(self,400,{'error':'Invalid match details'})
                pid=ObjectId(s['player_id']); r=players.find_one({'_id':pid})
                if not r:return json_send(self,404,{'error':'Player account not found'})
                scheduled_at = next_match_time(t)
                # A player can join only once in a particular tournament slot.
                if matches.find_one({'player_id':pid,'tournament':t,'scheduled_at':scheduled_at}):
                    return json_send(self,409,{'error':'You have already joined this match slot'})
                capacity=match_capacity(t)
                joined=matches.count_documents({'tournament':t,'scheduled_at':scheduled_at})
                if joined >= capacity:
                    return json_send(self,409,{'error':f'This {t} match is full. Please join the next scheduled slot.'})
                # Deduct points only after all slot checks pass.
                result=players.update_one({'_id':pid,'points':{'$gte':fee}},{'$inc':{'points':-fee}})
                if result.modified_count != 1:return json_send(self,400,{'error':'Insufficient wallet points'})
                matches.insert_one({'player_id':pid,'tournament':t,'squad':squad,'entry_fee':fee,'scheduled_at':scheduled_at,'status':'Upcoming','result':None,'prize':0,'created_at':now()})
                return json_send(self,200,{'ok':True,'scheduled_at':scheduled_at,'match_capacity':capacity,'players_joined':joined+1})
            if p == '/api/withdraw' and s:
                amt=int(d.get('amount',0)); method=d.get('method','UPI'); dest=d.get('destination','').strip(); pid=ObjectId(s['player_id'])
                if amt<50:return json_send(self,400,{'error':'Minimum withdrawal is ₹50'})
                r=players.find_one({'_id':pid})
                if amt>r.get('points',0):return json_send(self,400,{'error':'Insufficient wallet points'})
                if not dest:return json_send(self,400,{'error':'Destination is required'})
                if withdrawals.find_one({'player_id':pid,'status':'Pending'}):return json_send(self,400,{'error':'You already have a pending withdrawal'})
                players.update_one({'_id':pid,'points':{'$gte':amt}},{'$inc':{'points':-amt}})
                withdrawals.insert_one({'player_id':pid,'amount':amt,'method':method,'destination':dest,'status':'Pending','created_at':now(),'completed_at':None,'admin_note':None}); return json_send(self,200,{'ok':True})

            if not auth(self,True): return json_send(self,401,{'error':'Admin login required'})
            if p == '/api/admin/deposit/approve':
                x=deposits.find_one({'_id':oid(d.get('id')),'status':'Pending'})
                if not x:return json_send(self,400,{'error':'Deposit is not pending'})
                deposits.update_one({'_id':x['_id']},{'$set':{'status':'Approved','approved_at':now()}}); players.update_one({'_id':x['player_id']},{'$inc':{'points':x['amount']}}); admin_actions.insert_one({'action':'Approve deposit','target_type':'deposit','target_id':x['_id'],'created_at':now()}); return json_send(self,200,{'ok':True})
            if p == '/api/admin/deposit/reject':
                deposits.update_one({'_id':oid(d.get('id')),'status':'Pending'},{'$set':{'status':'Rejected'}}); return json_send(self,200,{'ok':True})
            if p == '/api/admin/withdraw/approve':
                x=withdrawals.find_one({'_id':oid(d.get('id')),'status':'Pending'})
                if not x:return json_send(self,400,{'error':'Withdrawal is not pending'})
                withdrawals.update_one({'_id':x['_id']},{'$set':{'status':'Completed','completed_at':now(),'admin_note':d.get('note','Approved and paid')}}); admin_actions.insert_one({'action':'Complete withdrawal','target_type':'withdrawal','target_id':x['_id'],'created_at':now()}); return json_send(self,200,{'ok':True})
            if p == '/api/admin/withdraw/reject':
                x=withdrawals.find_one({'_id':oid(d.get('id')),'status':'Pending'})
                if not x:return json_send(self,400,{'error':'Withdrawal is not pending'})
                withdrawals.update_one({'_id':x['_id']},{'$set':{'status':'Rejected','completed_at':now(),'admin_note':d.get('note','Rejected')}}); players.update_one({'_id':x['player_id']},{'$inc':{'points':x['amount']}}); return json_send(self,200,{'ok':True})
            if p == '/api/admin/player/add-points':
                pid=oid(d.get('id'))
                if not pid:return json_send(self,400,{'error':'Invalid player ID'})
                try: amount=int(d.get('amount',0))
                except Exception: return json_send(self,400,{'error':'Amount must be a whole number'})
                if amount<=0 or amount>1000000:return json_send(self,400,{'error':'Amount must be between 1 and 1,000,000'})
                note=str(d.get('note','')).strip()[:200]
                r=players.find_one({'_id':pid})
                if not r:return json_send(self,404,{'error':'Player not found'})
                players.update_one({'_id':pid},{'$inc':{'points':amount}})
                updated=players.find_one({'_id':pid},{'points':1})
                admin_actions.insert_one({'action':'Add extra points','target_type':'player','target_id':pid,'amount':amount,'note':note,'created_at':now()})
                return json_send(self,200,{'ok':True,'points':updated.get('points',0)})
            if p == '/api/admin/player/toggle-block':
                pid=oid(d.get('id'))
                if not pid:return json_send(self,400,{'error':'Invalid player ID'})
                r=players.find_one({'_id':pid})
                if not r:return json_send(self,404,{'error':'Player not found'})
                blocked=not bool(r.get('blocked',False))
                players.update_one({'_id':pid},{'$set':{'blocked':blocked,'blocked_at':now() if blocked else None}})
                if blocked:
                    for tok,session in list(SESSIONS.items()):
                        if session.get('player_id')==str(pid) and not session.get('admin'): SESSIONS.pop(tok,None)
                admin_actions.insert_one({'action':'Block player' if blocked else 'Unblock player','target_type':'player','target_id':pid,'created_at':now()})
                return json_send(self,200,{'ok':True,'blocked':blocked})
            if p == '/api/admin/player/remove':
                pid=oid(d.get('id'))
                if not pid:return json_send(self,400,{'error':'Invalid player ID'})
                r=players.find_one({'_id':pid})
                if not r:return json_send(self,404,{'error':'Player not found'})
                # Remove the player's account and all records owned by that account.
                deposits.delete_many({'player_id':pid})
                withdrawals.delete_many({'player_id':pid})
                matches.delete_many({'player_id':pid})
                players.delete_one({'_id':pid})
                for tok,session in list(SESSIONS.items()):
                    if session.get('player_id')==str(pid): SESSIONS.pop(tok,None)
                admin_actions.insert_one({'action':'Remove player','target_type':'player','target_id':pid,'created_at':now()})
                return json_send(self,200,{'ok':True})
            if p == '/api/admin/match/update':
                mid=oid(d.get('id')); m=matches.find_one({'_id':mid})
                if not m:return json_send(self,404,{'error':'Match not found'})
                matches.update_one({'_id':mid},{'$set':{'status':d.get('status','Completed'),'result':d.get('result',''),'prize':int(d.get('prize',0))}}); return json_send(self,200,{'ok':True})
            if p == '/api/admin/match/move-next':
                mid=oid(d.get('id')); m=matches.find_one({'_id':mid})
                if not m:return json_send(self,404,{'error':'Match not found'})
                t=m.get('tournament'); interval=30 if t=='BR ₹50 Room' else 20
                current=parse_iso(m.get('scheduled_at'))
                if not current:return json_send(self,400,{'error':'Match has no valid scheduled time'})
                candidate=current + datetime.timedelta(minutes=interval)
                now_local=datetime.datetime.now().astimezone()
                # Preserve the match slot timezone and operating window while looking for the next non-full slot.
                while True:
                    if candidate.hour < 12:
                        candidate=datetime.datetime.combine(candidate.date(),datetime.time(12,0),tzinfo=candidate.tzinfo)
                    elif candidate.hour >= 20:
                        candidate=datetime.datetime.combine(candidate.date()+datetime.timedelta(days=1),datetime.time(12,0),tzinfo=candidate.tzinfo)
                    slot=candidate.isoformat(timespec='seconds')
                    occupied=matches.count_documents({'tournament':t,'scheduled_at':slot,'_id':{'$ne':mid}})
                    if occupied < match_capacity(t): break
                    candidate += datetime.timedelta(minutes=interval)
                matches.update_one({'_id':mid},{'$set':{'scheduled_at':slot,'status':'Upcoming','result':None,'prize':0}})
                return json_send(self,200,{'ok':True,'scheduled_at':slot,'message':f'Player moved to {slot}'})
            if p == '/api/admin/room':
                t=d.get('tournament'); rid=d.get('room_id','').strip(); pw=d.get('password','').strip(); scheduled_at=str(d.get('scheduled_at','')).strip()
                if not t or not rid or not pw or not scheduled_at:return json_send(self,400,{'error':'Tournament, match date/time, room ID and password are required'})
                try:
                    datetime.datetime.fromisoformat(scheduled_at.replace('Z','+00:00'))
                except Exception:
                    return json_send(self,400,{'error':'Invalid match date/time'})
                rooms.update_one({'tournament':t,'scheduled_at':scheduled_at},{'$set':{'room_id':rid,'password':pw,'updated_at':now()}},upsert=True); return json_send(self,200,{'ok':True})
            return json_send(self,404,{'error':'Not found'})
        except Exception as e:
            print('API ERROR:',repr(e)); return json_send(self,500,{'error':str(e)})

if __name__ == '__main__':
    init_indexes()
    port = int(os.environ.get('PORT', '8000'))
    print(f'BOOYAH ARENA MongoDB server running on 0.0.0.0:{port}')
    print(f'MongoDB database: {DB_NAME}')
    ThreadingHTTPServer(('0.0.0.0', port),H).serve_forever()
