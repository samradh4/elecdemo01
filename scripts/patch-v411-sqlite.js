const fs = require('fs');

const target = process.argv[2] || 'App.tsx';
let s = fs.readFileSync(target, 'utf8');

const dbDecl = "let db: SQLite.SQLiteDatabase | null=null;";
if (!s.includes(dbDecl)) throw new Error('SQLite db declaration marker not found');
s = s.replace(dbDecl, `${dbDecl}\nlet dbPromise: Promise<SQLite.SQLiteDatabase> | null=null;`);

s = s.replace("aSyncInit();\nasync function aSyncInit(){try{await openDb()}catch{}}\n\n", "");

const start = s.indexOf('async function openDb(){');
const end = s.indexOf('\nasync function metaSet', start);
if (start < 0 || end < 0) throw new Error('openDb function markers not found');

const replacement = `async function openDb(){
  if(db)return db;
  if(!dbPromise){
    dbPromise=(async()=>{
      const opened=await SQLite.openDatabaseAsync('constituency-manager-v41.db');
      await opened.execAsync(\`PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS voters(
          id INTEGER PRIMARY KEY,constituencyId INTEGER,boothId INTEGER,serialNo TEXT,epicId TEXT,name TEXT,localName TEXT,
          relationType TEXT,relativeName TEXT,houseNo TEXT,age INTEGER,gender TEXT,section TEXT,surveyStatus TEXT,surveyNotes TEXT,
          surveyUpdatedAt TEXT,version INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_v41_voters_name ON voters(name);
        CREATE INDEX IF NOT EXISTS idx_v41_voters_local ON voters(localName);
        CREATE INDEX IF NOT EXISTS idx_v41_voters_epic ON voters(epicId);
        CREATE TABLE IF NOT EXISTS pending_changes(
          mutationId TEXT PRIMARY KEY,voterId INTEGER,status TEXT,notes TEXT,updatedAt TEXT
        );
        CREATE TABLE IF NOT EXISTS location_queue(
          pointId TEXT PRIMARY KEY,boothId INTEGER,shiftId TEXT,latitude REAL,longitude REAL,accuracy REAL,capturedAt TEXT
        );\`);
      db=opened;
      return opened;
    })().catch((e)=>{dbPromise=null;throw e;});
  }
  return await dbPromise;
}`;

s = s.slice(0, start) + replacement + s.slice(end);

if (!s.includes('let dbPromise: Promise<SQLite.SQLiteDatabase> | null=null;')) {
  throw new Error('SQLite promise mutex was not inserted');
}
if (s.includes('aSyncInit();')) throw new Error('Eager SQLite init is still present');

fs.writeFileSync(target, s);
console.log(`Patched ${target}: serialized SQLite initialization and removed eager open.`);
