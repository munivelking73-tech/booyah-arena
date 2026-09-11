import os, json, secrets, hashlib, datetime, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from bson import ObjectId
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from io import BytesIO
import qrcode
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.worksheet.datavalidation import DataValidation

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
referrals = db.referrals
tournament_configs = db.tournament_configs
admin_actions = db.admin_actions
SESSIONS = {}

# Tournament schedule timezone: India Standard Time (IST, UTC+05:30).
# Render servers normally run in UTC, so never use the server's local timezone
# for match-slot generation.
SCHEDULE_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def schedule_now():
    return datetime.datetime.now(SCHEDULE_TZ)

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

def tournament_defaults():
    return {
        'BR ₹25 Room': {'entry_fee':25,'first_prize':500,'second_prize':200,'per_kill':5,'capacity':48,'interval':30,'start_minute':0,'banner':'br-25-banner.png'},
        'BR ₹50 Room': {'entry_fee':50,'first_prize':800,'second_prize':300,'per_kill':10,'capacity':48,'interval':30,'start_minute':10,'banner':'br-50-banner.png'},
        'Lone Wolf ₹25': {'entry_fee':25,'first_prize':45,'second_prize':0,'per_kill':0,'capacity':2,'interval':25,'start_minute':20,'banner':'lone-wolf-banner.png'},
        'Lone Wolf ₹50': {'entry_fee':50,'first_prize':80,'second_prize':0,'per_kill':0,'capacity':2,'interval':25,'start_minute':25,'banner':'lone-wolf-banner.png'}
    }

def get_configs():
    defaults=tournament_defaults()
    out={}
    for t,cfg in defaults.items():
        saved=tournament_configs.find_one({'tournament':t},{'_id':0}) or {}
        merged=dict(cfg); merged.update({k:v for k,v in saved.items() if k!='tournament'})
        out[t]=merged
    return out

def config_for(tournament):
    cfg=get_configs().get(tournament)
    if not cfg: raise ValueError('Unknown tournament')
    return cfg

def next_match_time(tournament, from_dt=None):
    cfg=config_for(tournament); local=from_dt.astimezone(SCHEDULE_TZ) if from_dt else schedule_now(); day=local.date()
    base=datetime.datetime.combine(day,datetime.time(18,0),tzinfo=SCHEDULE_TZ)+datetime.timedelta(minutes=cfg['start_minute'])
    if local < base: return base.isoformat(timespec='seconds')
    elapsed=(local-base).total_seconds(); steps=int(elapsed//(cfg['interval']*60))+1
    candidate=base+datetime.timedelta(minutes=steps*cfg['interval'])
    if candidate >= datetime.datetime.combine(day,datetime.time(22,0),tzinfo=SCHEDULE_TZ):
        next_day=day+datetime.timedelta(days=1)
        candidate=datetime.datetime.combine(next_day,datetime.time(18,0),tzinfo=SCHEDULE_TZ)+datetime.timedelta(minutes=cfg['start_minute'])
    return candidate.isoformat(timespec='seconds')

def match_capacity(tournament):
    return config_for(tournament)['capacity']

def match_duration(tournament):
    return config_for(tournament)['interval']

def upcoming_slots(tournament, count=12, from_dt=None):
    cfg=config_for(tournament); first=parse_iso(next_match_time(tournament, from_dt)); out=[]
    if not first: return out
    for i in range(count):
        slot=first+datetime.timedelta(minutes=cfg['interval']*i)
        # Stop at the daily 10 PM boundary; next day slots are generated only if explicitly needed.
        if slot.astimezone(SCHEDULE_TZ).date()!=first.astimezone(SCHEDULE_TZ).date(): break
        if slot.astimezone(SCHEDULE_TZ).time() >= datetime.time(22,0): break
        out.append(slot.isoformat(timespec='seconds'))
    return out

def parse_iso(value):
    try:
        return datetime.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except Exception:
        return None

def live_match_status(row):
    status=row.get('status','Upcoming')
    if status in ('Completed','Won','Lost','Cancelled'):
        return status
    start=parse_iso(row.get('scheduled_at'))
    if not start:
        return status
    now_dt=datetime.datetime.now(datetime.timezone.utc)
    start_utc=start.astimezone(datetime.timezone.utc) if start.tzinfo else start.replace(tzinfo=datetime.timezone.utc)
    duration = match_duration(row.get('tournament'))
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
    return {'id': str(r['_id']), 'username': r['username'], 'name': r['name'], 'email': r['email'], 'uid': r['uid'], 'points': r.get('points',0), 'blocked': bool(r.get('blocked', False)), 'created': r['created_at'], 'last_login_at': r.get('last_login_at'), 'referral_code': r.get('referral_code',''), 'referral_points': r.get('referral_points',0)}

def init_indexes():
    players.create_index([('username', ASCENDING)], unique=True)
    players.create_index([('email', ASCENDING)], unique=True)
    players.create_index([('uid', ASCENDING)], unique=True)
    deposits.create_index([('player_id', ASCENDING)])
    matches.create_index([('player_id', ASCENDING)])
    withdrawals.create_index([('player_id', ASCENDING)])
    referrals.create_index([('referrer_id', ASCENDING)])
    referrals.create_index([('referred_id', ASCENDING)], unique=True)
    tournament_configs.create_index([('tournament', ASCENDING)], unique=True)
    try:
        rooms.drop_index('tournament_1')
    except Exception:
        pass
    rooms.create_index([('tournament', ASCENDING), ('scheduled_at', ASCENDING)], unique=True)
    # Verify the server is reachable at startup.
    client.admin.command('ping')


def build_match_workbook(tournament=None, scheduled_at=None, upcoming_only=False):
    """Create an admin Excel workbook containing joined players for one or all upcoming slots."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'Joined Players'
    headers = [
        'Match date', 'Match time (IST)', 'Tournament', 'Player name',
        'Free Fire UID', 'Username', 'Email', 'Squad', 'Entry fee (₹)',
        'Match status', 'UID verified', 'Name verified', 'Entered match'
    ]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal='center', vertical='center')
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = 'A1:M1'

    tournaments = list(tournament_defaults().keys())
    query = {}
    if tournament and scheduled_at:
        query = {'tournament': tournament, 'scheduled_at': scheduled_at}
    elif upcoming_only:
        for t in tournaments:
            for slot in upcoming_slots(t,12):
                for m in matches.find({'tournament': t, 'scheduled_at': slot}).sort('_id', 1):
                    p = players.find_one({'_id': m.get('player_id')}) or {}
                    start = parse_iso(slot)
                    ws.append([
                        start.astimezone(SCHEDULE_TZ).strftime('%d-%m-%Y') if start else '',
                        start.astimezone(SCHEDULE_TZ).strftime('%I:%M %p') if start else '',
                        t, p.get('name',''), p.get('uid',''), p.get('username',''),
                        p.get('email',''), m.get('squad',''), int(m.get('entry_fee', 0)), live_match_status(m), '', '', ''
                    ])
    else:
        rows = list(matches.find(query).sort('scheduled_at', 1).sort('_id', 1))
        for m in rows:
            p = players.find_one({'_id': m.get('player_id')}) or {}
            start = parse_iso(m.get('scheduled_at'))
            ws.append([
                start.astimezone(SCHEDULE_TZ).strftime('%d-%m-%Y') if start else '',
                start.astimezone(SCHEDULE_TZ).strftime('%I:%M %p') if start else '',
                m.get('tournament',''), p.get('name',''), p.get('uid',''),
                p.get('username',''), p.get('email',''), m.get('squad',''),
                int(m.get('entry_fee', 0)), live_match_status(m), '', '', ''
            ])

    for col, width in enumerate([14,16,22,22,16,18,28,16,14,16,16,16,16], 1):
        ws.column_dimensions[chr(64+col)].width = width
    dv = DataValidation(type='list', formula1='"YES,NO"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add('K2:M1048576')

    info = wb.create_sheet('Instructions')
    info.append(['BOOYAH ARENA — PLAYER VERIFICATION'])
    info['A1'].font = Font(bold=True, size=14)
    info.append(['Use this sheet before the match to compare the registered player name and Free Fire UID with the player who enters the room.'])
    info.append(['Mark UID verified, Name verified, and Entered match as YES or NO.'])
    info.column_dimensions['A'].width = 115
    return wb

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
        if p == '/api/admin/export-match':
            if not auth(self, True): return json_send(self,401,{'error':'Admin login required'})
            q = parse_qs(urlparse(self.path).query)
            tournament = q.get('tournament',[''])[0]
            scheduled_at = q.get('scheduled_at',[''])[0]
            if not tournament or not scheduled_at:
                return json_send(self,400,{'error':'Tournament and scheduled_at are required'})
            wb = build_match_workbook(tournament, scheduled_at)
            bio = BytesIO()
            wb.save(bio)
            data = bio.getvalue()
            safe_t = re.sub(r'[^A-Za-z0-9]+','-', tournament).strip('-') or 'match'
            safe_dt = re.sub(r'[^0-9A-Za-z]+','-', scheduled_at).strip('-')
            filename = f'BOOYAH-ARENA-{safe_t}-{safe_dt}.xlsx'
            self.send_response(200)
            self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if p == '/api/admin/export-upcoming':
            if not auth(self, True): return json_send(self,401,{'error':'Admin login required'})
            wb = build_match_workbook(upcoming_only=True)
            bio = BytesIO()
            wb.save(bio)
            data = bio.getvalue()
            self.send_response(200)
            self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            self.send_header('Content-Disposition','attachment; filename="BOOYAH-ARENA-upcoming-player-verification.xlsx"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
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
        if p == '/api/tournament-configs':
            return json_send(self,200,{'configs':get_configs()})
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
            tournaments=list(tournament_defaults().keys())
            now_local=schedule_now()
            out=[]
            for t in tournaments:
                cfg=config_for(t); interval=cfg['interval']
                candidate=parse_iso(next_match_time(t))
                for _ in range(4):
                    if not candidate: break
                    scheduled=candidate.isoformat(timespec='seconds')
                    joined=matches.count_documents({'tournament':t,'scheduled_at':scheduled})
                    mine=matches.find_one({'player_id':ObjectId(s['player_id']),'tournament':t,'scheduled_at':scheduled},{'_id':1}) is not None
                    cfg=config_for(t)
                    prize=f"1st ₹{cfg['first_prize']}" + (f" · 2nd ₹{cfg['second_prize']}" if cfg['second_prize'] else '') + (f" · ₹{cfg['per_kill']}/kill" if cfg['per_kill'] else '')
                    out.append({'tournament':t,'scheduled_at':scheduled,'entry_fee':cfg['entry_fee'],'capacity':cfg['capacity'],'joined':joined,'joined_by_player':mine,'prize':prize,'first_prize':cfg['first_prize'],'second_prize':cfg['second_prize'],'per_kill':cfg['per_kill'],'banner':cfg['banner']})
                    candidate += datetime.timedelta(minutes=interval)
                    if candidate.hour >= 22:
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
            tournaments=list(tournament_defaults().keys())
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
            ps = list(players.find({}, {'username':1,'name':1,'email':1,'uid':1,'points':1,'blocked':1,'created_at':1,'last_login_at':1}).sort('_id',-1))
            active_player_ids={sess.get('player_id') for sess in SESSIONS.values() if not sess.get('admin') and sess.get('player_id')}
            for pp in ps:
                pp['logged_in']=str(pp['_id']) in active_player_ids
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
            tournaments=list(tournament_defaults().keys())
            now_local=schedule_now()
            now_utc=datetime.datetime.now(datetime.timezone.utc)
            grouped={}

            def add_group(t, scheduled):
                key=(t,scheduled)
                if key not in grouped:
                    grouped[key]={'tournament':t,'scheduled_at':scheduled,'capacity':match_capacity(t),'players':[]}
                return grouped[key]

            # Generate the next 12 slots for each format. The first slot is the next slot at/after now.
            for t in tournaments:
                for slot in upcoming_slots(t,12):
                    add_group(t,slot)

            # Merge current/new-format player matches into slot control.
            # Historical records remain available in Match database / results, but
            # obsolete pre-6PM/old-timezone slots must not reappear in the live timetable.
            for x in mt:
                t=x.get('tournament')
                scheduled=x.get('scheduled_at')
                if t not in tournaments or not scheduled:
                    continue
                start=parse_iso(scheduled)
                if not start:
                    continue
                start_ist=start.astimezone(SCHEDULE_TZ) if start.tzinfo else start.replace(tzinfo=SCHEDULE_TZ)
                # Keep only valid 6:00 PM-9:40 PM IST slots from today onward.
                if start_ist.time() < datetime.time(18,0) or start_ist.time() >= datetime.time(22,0):
                    continue
                if start_ist.date() < now_local.date():
                    continue
                key=(t,scheduled)
                g=add_group(t,scheduled)
                g['players'].append({'id':str(x.get('_id')),'name':x.get('name'),'uid':x.get('uid'),'squad':x.get('squad'),'status':live_match_status(x)})

            room_docs=list(rooms.find({}, {'_id':0,'tournament':1,'scheduled_at':1,'room_id':1,'password':1,'updated_at':1}))
            room_map={(r.get('tournament'),r.get('scheduled_at')):r for r in room_docs}
            groups=[]
            for g in grouped.values():
                start=parse_iso(g['scheduled_at'])
                if not start:
                    continue
                start_utc=start.astimezone(datetime.timezone.utc) if start.tzinfo else start.replace(tzinfo=datetime.timezone.utc)
                duration=datetime.timedelta(minutes=match_duration(g['tournament']))
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
            ref_rows=[]
            for rr in referrals.find({}).sort('_id',-1):
                ref=players.find_one({'_id':rr.get('referrer_id')}) or {}; newp=players.find_one({'_id':rr.get('referred_id')}) or {}
                ref_rows.append({'id':str(rr['_id']),'referrer_name':ref.get('name',''),'referrer_username':ref.get('username',''),'referrer_uid':ref.get('uid',''),'referred_name':newp.get('name',''),'referred_username':newp.get('username',''),'referred_uid':newp.get('uid',''),'points_awarded':rr.get('points_awarded',10),'created_at':rr.get('created_at')})
            return json_send(self,200,{'players':ps,'deposits':dep,'withdrawals':wd,'matches':mt,'match_groups':groups,'configs':get_configs(),'referrals':ref_rows})
        return json_send(self,404,{'error':'Not found'})

    def api_post(self,p,d):
        try:
            if p == '/api/register':
                for k in ('username','name','email','uid','password'):
                    if not d.get(k): return json_send(self,400,{'error':'All fields are required'})
                if len(d['password']) < 6: return json_send(self,400,{'error':'Password must be at least 6 characters'})
                uid = str(d['uid']).strip()
                if not uid.isdigit() or len(uid) != 10: return json_send(self,400,{'error':'Free Fire UID must be exactly 10 digits'})
                doc = {'username':d['username'].strip().lower(),'name':d['name'].strip(),'email':d['email'].strip().lower(),'uid':uid,'password_hash':ph(d['password']),'points':0,'blocked':False,'created_at':now(),'referral_code':'','referral_points':0}
                try: r = players.insert_one(doc)
                except DuplicateKeyError: return json_send(self,409,{'error':'Username, email or UID already exists'})
                doc['_id'] = r.inserted_id
                referral_code='BOOYAH-'+str(r.inserted_id)[-8:].upper()
                players.update_one({'_id':r.inserted_id},{'$set':{'referral_code':referral_code}}); doc['referral_code']=referral_code
                referral_input=str(d.get('referral_code','')).strip().upper()
                if referral_input:
                    ref=players.find_one({'referral_code':referral_input})
                    if not ref or ref['_id']==r.inserted_id:
                        players.delete_one({'_id':r.inserted_id}); return json_send(self,400,{'error':'Invalid referral code'})
                    try:
                        referrals.insert_one({'referrer_id':ref['_id'],'referred_id':r.inserted_id,'referral_code':referral_input,'points_awarded':10,'created_at':now()})
                        players.update_one({'_id':ref['_id' ]},{'$inc':{'points':10,'referral_points':10}})
                    except DuplicateKeyError:
                        pass
                tok = secrets.token_urlsafe(32); SESSIONS[tok]={'player_id':str(r.inserted_id),'admin':False}
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
                login_time=now(); players.update_one({'_id':r['_id']},{'$set':{'last_login_at':login_time}}); r['last_login_at']=login_time
                tok=secrets.token_urlsafe(32); SESSIONS[tok]={'player_id':str(r['_id']),'admin':False,'login_at':login_time}; return json_send(self,200,{'token':tok,'player':player_obj(r)})
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
                t=d.get('tournament'); cfg=config_for(t) if t in tournament_defaults() else None; fee=cfg['entry_fee'] if cfg else None; squad=d.get('squad','').strip()
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
            if p == '/api/admin/referrals':
                rows=[]
                for r in referrals.find({}).sort('_id',-1):
                    ref=players.find_one({'_id':r.get('referrer_id')}) or {}; newp=players.find_one({'_id':r.get('referred_id')}) or {}
                    rows.append({'id':str(r['_id']),'referrer_name':ref.get('name',''),'referrer_username':ref.get('username',''),'referrer_uid':ref.get('uid',''),'referred_name':newp.get('name',''),'referred_username':newp.get('username',''),'referred_uid':newp.get('uid',''),'points_awarded':r.get('points_awarded',10),'created_at':r.get('created_at')})
                return json_send(self,200,{'referrals':rows})
            if p == '/api/admin/tournament-config':
                t=str(d.get('tournament','')); cfg=config_for(t) if t in tournament_defaults() else None
                if not cfg:return json_send(self,400,{'error':'Unknown tournament'})
                fields=('entry_fee','first_prize','second_prize','per_kill')
                vals={}
                for f in fields:
                    try: v=int(d.get(f,cfg[f]))
                    except Exception:return json_send(self,400,{'error':'All price values must be whole numbers'})
                    if v<0 or v>1000000:return json_send(self,400,{'error':'Price values must be between 0 and 1,000,000'})
                    vals[f]=v
                tournament_configs.update_one({'tournament':t},{'$set':{'tournament':t,**vals,'updated_at':now()}},upsert=True)
                return json_send(self,200,{'ok':True,'config':config_for(t)})
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
                t=m.get('tournament'); interval=config_for(t)['interval']
                current=parse_iso(m.get('scheduled_at'))
                if not current:return json_send(self,400,{'error':'Match has no valid scheduled time'})
                candidate=current + datetime.timedelta(minutes=interval)
                # Preserve the match slot timezone and operating window while looking for the next non-full slot.
                while True:
                    if candidate.hour < 18:
                        candidate=datetime.datetime.combine(candidate.date(),datetime.time(18,0),tzinfo=candidate.tzinfo)
                    elif candidate.hour >= 22:
                        candidate=datetime.datetime.combine(candidate.date()+datetime.timedelta(days=1),datetime.time(18,0),tzinfo=candidate.tzinfo)
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
