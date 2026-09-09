BOOYAH ARENA - MONGODB WINDOWS SETUP

1) Install MongoDB Community Server and MongoDB Compass on Windows.
   During installation, choose Complete and Install MongoD as a Service.

2) Open MongoDB Compass.
   Connect using:
   mongodb://127.0.0.1:27017

3) In PowerShell, open this project folder and create a Python virtual environment:
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1

4) Install the MongoDB Python driver:
   python -m pip install -r requirements.txt

5) Start the BOOYAH ARENA server:
   python server.py

6) Open the website:
   http://127.0.0.1:8000

7) In Compass, refresh Databases. The database named 'booyah_arena' appears after the server starts and creates indexes.
   Collections:
   players
   deposits
   matches
   withdrawals
   rooms
   admin_actions

MongoDB connection used by server.py:
   mongodb://127.0.0.1:27017/
Database name:
   booyah_arena

Optional environment variables:
   MONGO_URI       Change MongoDB connection string
   MONGO_DB        Change database name
   BOOYAH_ADMIN_USER
   BOOYAH_ADMIN_PASS

PowerShell example for MongoDB Atlas instead of local MongoDB:
   $env:MONGO_URI='mongodb+srv://USERNAME:PASSWORD@YOURCLUSTER.mongodb.net/?retryWrites=true&w=majority'
   python server.py

IMPORTANT:
- Do not put the MongoDB URI or password in browser HTML/JavaScript.
- MongoDB Compass is the database viewer; server.py is the application that talks to MongoDB.
- The default local MongoDB setup is localhost-only, which is appropriate for a desktop development setup.

MATCH ROOM DETAILS UPDATE
--------------------------
When a player joins a tournament, the server assigns that entry to a specific match date/time.
In Admin > Match database / results, each scheduled match has a Room button.
Click Room, enter the Free Fire Room ID and password, and save.
The player dashboard will then show the Room ID and password inside that player's exact match entry,
along with the exact match date/time. Room details are not shared across different match times.

Only the three active formats above can be newly joined. Older database records from removed formats are retained as historical records and are not offered on the website.
