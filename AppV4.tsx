import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator, Alert, FlatList, Linking, Modal, Pressable, SafeAreaView,
  ScrollView, StyleSheet, Text, TextInput, View
} from 'react-native';
import * as SecureStore from 'expo-secure-store';
import * as SQLite from 'expo-sqlite';
import NetInfo from '@react-native-community/netinfo';
import { StatusBar } from 'expo-status-bar';

const API='https://elecdemo01.onrender.com/v4';
const PURPLE='#6D28D9', BG='#F6F5FA', BORDER='#E7E4EF', TEXT='#17131F', MUTED='#756E82', GREEN='#16A34A', RED='#DC2626', AMBER='#D97706';
const TOKEN_KEY='cm4-token';
let db: SQLite.SQLiteDatabase | null=null;

type User={id:number;username:string;fullName:string;phone:string;role:'admin'|'volunteer';active:boolean};
type Booth={id:number;constituencyId:number;boothNo:string;name:string;address:string;active:boolean};
type Constituency={id:number;code:string;name:string;active:boolean;booths:Booth[]};
type Assignment={id:number;userId:number;constituency:{id:number;code:string;name:string};booth:Booth;status:string;requestedAt?:string;approvedAt?:string};
type Voter={id:number;constituencyId:number;boothId:number;serialNo:string;epicId:string;name:string;localName:string;relationType:string;relativeName:string;houseNo:string;age:number;gender:string;section:string;surveyStatus:string;surveyNotes:string;surveyUpdatedAt?:string;version:number};
type Session={user:User;assignment:Assignment|null;offlineLeaseUntil?:string|null};

async function openDb(){
  if(db) return db;
  db=await SQLite.openDatabaseAsync('constituency-manager-v4.db');
  await db.execAsync(`PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS voters(
      id INTEGER PRIMARY KEY,constituencyId INTEGER,boothId INTEGER,serialNo TEXT,epicId TEXT,name TEXT,localName TEXT,
      relationType TEXT,relativeName TEXT,houseNo TEXT,age INTEGER,gender TEXT,section TEXT,surveyStatus TEXT,surveyNotes TEXT,
      surveyUpdatedAt TEXT,version INTEGER DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_local_voters_name ON voters(name);
    CREATE INDEX IF NOT EXISTS idx_local_voters_epic ON voters(epicId);
    CREATE TABLE IF NOT EXISTS pending_changes(
      mutationId TEXT PRIMARY KEY,voterId INTEGER,status TEXT,notes TEXT,updatedAt TEXT
    );`);
  return db;
}
async function metaSet(key:string,value:any){const d=await openDb();await d.runAsync('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',key,JSON.stringify(value));}
async function metaGet<T>(key:string):Promise<T|null>{const d=await openDb();const r=await d.getFirstAsync<any>('SELECT value FROM meta WHERE key=?',key);if(!r)return null;try{return JSON.parse(r.value)}catch{return null}}
async function clearLocalBooth(){const d=await openDb();await d.execAsync('DELETE FROM voters; DELETE FROM pending_changes;');}
async function replaceLocalVoters(rows:Voter[]){const d=await openDb();await d.withTransactionAsync(async()=>{await d.execAsync('DELETE FROM voters;');for(const v of rows){await d.runAsync(`INSERT INTO voters(id,constituencyId,boothId,serialNo,epicId,name,localName,relationType,relativeName,houseNo,age,gender,section,surveyStatus,surveyNotes,surveyUpdatedAt,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,v.id,v.constituencyId,v.boothId,v.serialNo||'',v.epicId||'',v.name||'',v.localName||'',v.relationType||'',v.relativeName||'',v.houseNo||'',v.age||0,v.gender||'',v.section||'',v.surveyStatus||'Pending',v.surveyNotes||'',v.surveyUpdatedAt||'',v.version||1);}})}
async function localSearch(q=''){const d=await openDb();if(!q.trim())return await d.getAllAsync<Voter>('SELECT * FROM voters ORDER BY CAST(serialNo AS INTEGER), name LIMIT 2000');const p='%'+q.trim()+'%';return await d.getAllAsync<Voter>('SELECT * FROM voters WHERE name LIKE ? OR localName LIKE ? OR epicId LIKE ? OR relativeName LIKE ? OR houseNo LIKE ? ORDER BY CAST(serialNo AS INTEGER), name LIMIT 500',p,p,p,p,p)}
async function queueMutation(voterId:number,status:string,notes:string){const d=await openDb();const id=`${voterId}-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;const now=new Date().toISOString();await d.runAsync('INSERT INTO pending_changes(mutationId,voterId,status,notes,updatedAt) VALUES (?,?,?,?,?)',id,voterId,status,notes,now);await d.runAsync('UPDATE voters SET surveyStatus=?,surveyNotes=?,surveyUpdatedAt=? WHERE id=?',status,notes,now,voterId);return id}
async function pendingCount(){const d=await openDb();const r=await d.getFirstAsync<any>('SELECT COUNT(*) n FROM pending_changes');return Number(r?.n||0)}

function msg(title:string,text:string){Alert.alert(title,text)}
async function api(path:string,token:string,options:any={}){
  const controller=new AbortController();const t=setTimeout(()=>controller.abort(),options.timeoutMs||30000);
  try{
    const r=await fetch(API+path,{...options,signal:controller.signal,headers:{Accept:'application/json',...(token?{Authorization:`Bearer ${token}`}:{ }),...(options.headers||{})}});
    const ct=r.headers.get('content-type')||'';const body=ct.includes('json')?await r.json():await r.text();
    if(!r.ok)throw new Error(typeof body==='string'?body:(body.detail||`HTTP ${r.status}`));return body;
  }catch(e:any){if(e?.name==='AbortError')throw new Error('Server timed out. Check internet and retry.');throw e}finally{clearTimeout(t)}
}

export default function App(){
  const [ready,setReady]=useState(false),[busy,setBusy]=useState(false),[online,setOnline]=useState(false);
  const [token,setToken]=useState(''),[session,setSession]=useState<Session|null>(null),[catalog,setCatalog]=useState<Constituency[]>([]);
  const [mode,setMode]=useState<'login'|'register'>('login');
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[fullName,setFullName]=useState(''),[phone,setPhone]=useState('');
  const [selectedConst,setSelectedConst]=useState<number|null>(null),[boothSearch,setBoothSearch]=useState('');
  const [query,setQuery]=useState(''),[voters,setVoters]=useState<Voter[]>([]),[selected,setSelected]=useState<Voter|null>(null),[pending,setPending]=useState(0);
  const leaseValid=useMemo(()=>!session?.offlineLeaseUntil||new Date(session.offlineLeaseUntil).getTime()>Date.now(),[session]);

  const loadLocal=useCallback(async()=>{setVoters(await localSearch(query));setPending(await pendingCount())},[query]);
  const refreshSession=useCallback(async(tk=token)=>{if(!tk)return null;const me=await api('/me',tk,{timeoutMs:60000});const asg=await api('/my/assignment',tk,{timeoutMs:60000});const s:Session={user:me.user,assignment:asg.active||null,offlineLeaseUntil:asg.offlineLeaseUntil||null};setSession(s);await metaSet('session',s);return {...s,pendingAssignment:asg.pending};},[token]);

  const syncNow=useCallback(async(silent=false)=>{
    if(!token)return;setBusy(!silent);
    try{
      const asg=await api('/my/assignment',token,{timeoutMs:90000});
      const cached=await metaGet<Session>('session');
      if(!asg.active){if(cached?.assignment){await clearLocalBooth()}const s={user:session!.user,assignment:null,offlineLeaseUntil:null};setSession(s);await metaSet('session',s);await loadLocal();if(!silent)msg('Access updated',asg.pending?'Your booth request is waiting for admin approval.':'No active booth is assigned.');return}
      const changed=!!cached?.assignment&&cached.assignment.booth.id!==asg.active.booth.id;
      if(changed)await clearLocalBooth();
      const d=await openDb();const changes=await d.getAllAsync<any>('SELECT * FROM pending_changes ORDER BY updatedAt LIMIT 5000');
      let items:Voter[]|null=null,lease=asg.offlineLeaseUntil;
      if(changes.length){const out=await api('/sync',token,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mutations:changes}),timeoutMs:90000});items=out.items;lease=out.offlineLeaseUntil;for(const id of out.accepted||[])await d.runAsync('DELETE FROM pending_changes WHERE mutationId=?',id);if((out.rejected||[]).length&&!silent)msg('Some changes need attention',`${out.rejected.length} offline change(s) were rejected.`)}
      if(!items){const out=await api('/my/voters',token,{timeoutMs:90000});items=out.items;lease=out.offlineLeaseUntil}
      await replaceLocalVoters(items||[]);
      const s:Session={user:session!.user,assignment:asg.active,offlineLeaseUntil:lease};setSession(s);await metaSet('session',s);await loadLocal();if(!silent)msg('Synced',`Booth ${asg.active.booth.boothNo} is up to date.`)
    }catch(e:any){if(!silent)msg('Sync failed',e.message)}finally{setBusy(false)}
  },[token,session,loadLocal]);

  useEffect(()=>{(async()=>{await openDb();const tk=await SecureStore.getItemAsync(TOKEN_KEY)||'';setToken(tk);const cached=await metaGet<Session>('session');if(cached)setSession(cached);await loadLocal();if(tk){try{const r=await refreshSession(tk);if(r?.user?.role==='volunteer'&&r.assignment)await syncNow(true)}catch{}}setReady(true)})()},[]);
  useEffect(()=>{const sub=NetInfo.addEventListener(state=>{const is=!!state.isConnected;setOnline(is);if(is&&token&&session?.user?.role==='volunteer')syncNow(true)});return()=>sub()},[token,session?.user?.role,syncNow]);
  useEffect(()=>{loadLocal()},[query]);
  useEffect(()=>{if(token&&session?.user?.role==='volunteer'&&!session.assignment&&online){api('/catalog',token).then(setCatalog).catch(()=>{})}},[token,session?.assignment,session?.user?.role,online]);

  async function authenticate(kind:'login'|'register'){
    setBusy(true);try{const body=kind==='login'?{username,password}:{username,password,fullName,phone};const out=await api(kind==='login'?'/auth/login':'/auth/register','',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),timeoutMs:90000});await SecureStore.setItemAsync(TOKEN_KEY,out.token);setToken(out.token);const s:Session={user:out.user,assignment:null,offlineLeaseUntil:null};setSession(s);await metaSet('session',s);if(out.bootstrapAdmin)msg('Admin account created','This first account is the administrator. Open the Admin Portal to create constituencies, upload booth data and manage volunteers.');await refreshSession(out.token)}catch(e:any){msg('Could not continue',e.message)}finally{setBusy(false)}}
  async function logout(){await SecureStore.deleteItemAsync(TOKEN_KEY);setToken('');setSession(null);setCatalog([]);setSelected(null);setUsername('');setPassword('')}
  async function requestBooth(booth:Booth){if(!selectedConst)return;setBusy(true);try{await api('/assignments/request',token,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({constituencyId:selectedConst,boothId:booth.id})});await refreshSession();msg('Request sent',`Booth ${booth.boothNo} has been sent to admin for approval.`)}catch(e:any){msg('Could not request booth',e.message)}finally{setBusy(false)}}
  async function saveSurvey(v:Voter,status:string,notes:string){await queueMutation(v.id,status,notes);setSelected(null);await loadLocal();if(online)syncNow(true)}

  if(!ready)return <Center><ActivityIndicator color={PURPLE}/><Text style={styles.muted}>Preparing offline database…</Text></Center>;
  if(!token||!session)return <Auth mode={mode} setMode={setMode} username={username} setUsername={setUsername} password={password} setPassword={setPassword} fullName={fullName} setFullName={setFullName} phone={phone} setPhone={setPhone} busy={busy} onGo={()=>authenticate(mode)}/>;
  if(session.user.role==='admin')return <AdminHome user={session.user} online={online} onLogout={logout}/>;
  if(!session.assignment)return <AssignmentHome user={session.user} catalog={catalog} selectedConst={selectedConst} setSelectedConst={setSelectedConst} boothSearch={boothSearch} setBoothSearch={setBoothSearch} busy={busy} online={online} onRequest={requestBooth} onRefresh={()=>refreshSession()} onLogout={logout}/>;
  if(!leaseValid&&!online)return <Center><Text style={styles.title}>Internet verification required</Text><Text style={styles.muted}>Your offline booth access lease has expired. Connect to the internet once to verify the current assignment.</Text><Button label="Logout" secondary onPress={logout}/></Center>;

  return <SafeAreaView style={styles.safe}><StatusBar style="dark"/><View style={styles.header}><View style={{flex:1}}><Text style={styles.appTitle}>Booth {session.assignment.booth.boothNo}</Text><Text style={styles.muted}>{session.assignment.constituency.name}</Text></View><Pill text={online?'ONLINE':'OFFLINE'} good={online}/></View>
    <View style={styles.syncBar}><Text style={styles.syncText}>{pending?`${pending} change${pending===1?'':'s'} waiting to sync`:'All saved changes synced'}</Text><Button label={busy?'Syncing…':'Sync now'} small onPress={()=>syncNow()} disabled={busy||!online}/></View>
    <View style={styles.searchRow}><TextInput style={styles.input} value={query} onChangeText={setQuery} placeholder="Search voter name / EPIC / house" placeholderTextColor="#aaa"/><Text style={styles.count}>{voters.length}</Text></View>
    <FlatList data={voters} keyExtractor={x=>String(x.id)} contentContainerStyle={{padding:12,paddingBottom:100,gap:8}} renderItem={({item})=><Pressable onPress={()=>setSelected(item)}><View style={styles.card}><View style={{flexDirection:'row',justifyContent:'space-between',gap:8}}><Text style={styles.meta}>Serial {item.serialNo||'—'} · {item.epicId}</Text><StatusPill status={item.surveyStatus}/></View><Text style={styles.voterName}>{item.name}</Text>{item.localName&&item.localName!==item.name?<Text style={styles.local}>{item.localName}</Text>:null}<Text style={styles.line}>Relative: {item.relativeName||'—'} · House: {item.houseNo||'—'}</Text><Text style={styles.line}>{item.gender} {item.age?`· Age ${item.age}`:''}</Text></View></Pressable>} ListEmptyComponent={<Center><Text style={styles.muted}>No voters downloaded for this booth yet.</Text></Center>}/>
    <View style={styles.bottom}><Button label="Refresh assignment" secondary onPress={()=>syncNow()}/><Button label="Logout" secondary onPress={logout}/></View>
    <SurveyModal voter={selected} onClose={()=>setSelected(null)} onSave={saveSurvey}/>
  </SafeAreaView>
}

function Auth(p:any){return <SafeAreaView style={styles.safe}><StatusBar style="dark"/><ScrollView contentContainerStyle={styles.auth}><Text style={styles.logo}>Constituency Manager</Text><Text style={styles.muted}>Offline booth survey & voter-list workflow</Text><View style={styles.card}><View style={styles.segment}><Pressable onPress={()=>p.setMode('login')} style={[styles.seg,p.mode==='login'&&styles.segOn]}><Text style={p.mode==='login'?styles.segOnText:styles.segText}>Login</Text></Pressable><Pressable onPress={()=>p.setMode('register')} style={[styles.seg,p.mode==='register'&&styles.segOn]}><Text style={p.mode==='register'?styles.segOnText:styles.segText}>Register</Text></Pressable></View>{p.mode==='register'&&<><Field label="Full name" value={p.fullName} onChange={p.setFullName}/><Field label="Phone" value={p.phone} onChange={p.setPhone}/></>}<Field label="Username" value={p.username} onChange={p.setUsername}/><Field label="Password" value={p.password} onChange={p.setPassword} secure/><Button label={p.busy?'Please wait…':p.mode==='login'?'Login':'Create account'} disabled={p.busy} onPress={p.onGo}/><Text style={[styles.muted,{marginTop:12}]}>The first v4 account becomes the administrator. Later registrations are volunteer accounts.</Text></View></ScrollView></SafeAreaView>}
function AdminHome({user,online,onLogout}:any){return <SafeAreaView style={styles.safe}><StatusBar style="dark"/><View style={styles.header}><View><Text style={styles.appTitle}>Admin</Text><Text style={styles.muted}>{user.fullName}</Text></View><Pill text={online?'ONLINE':'OFFLINE'} good={online}/></View><ScrollView contentContainerStyle={{padding:16,gap:12}}><View style={styles.card}><Text style={styles.title}>Admin web portal</Text><Text style={styles.muted}>Create constituencies and booths, upload booth-wise voter CSVs, approve volunteer requests, assign/reassign booths, monitor progress and download survey data.</Text><Button label="Open Admin Portal" onPress={()=>Linking.openURL('https://elecdemo01.onrender.com/v4/admin')}/></View><View style={styles.card}><Text style={styles.title}>Volunteer access model</Text><Text style={styles.muted}>Volunteers only download the booth assigned to them. Reassignment revokes the old booth on the next sync; offline access also expires after the configured lease period.</Text></View><Button label="Logout" secondary onPress={onLogout}/></ScrollView></SafeAreaView>}
function AssignmentHome({user,catalog,selectedConst,setSelectedConst,boothSearch,setBoothSearch,busy,online,onRequest,onRefresh,onLogout}:any){const c=catalog.find((x:any)=>x.id===selectedConst);const booths=(c?.booths||[]).filter((b:any)=>!boothSearch||`${b.boothNo} ${b.name}`.toLowerCase().includes(boothSearch.toLowerCase()));return <SafeAreaView style={styles.safe}><StatusBar style="dark"/><View style={styles.header}><View><Text style={styles.appTitle}>Hello, {user.fullName}</Text><Text style={styles.muted}>Choose where you are assigned to work</Text></View><Pill text={online?'ONLINE':'OFFLINE'} good={online}/></View><ScrollView contentContainerStyle={{padding:14,paddingBottom:100}}><View style={styles.card}><Text style={styles.title}>1. Select constituency</Text>{!online?<Text style={styles.warn}>Internet is required to request or refresh a booth assignment.</Text>:catalog.length===0?<Text style={styles.muted}>No constituencies have been uploaded by admin yet.</Text>:catalog.map((x:any)=><Pressable key={x.id} onPress={()=>setSelectedConst(x.id)} style={[styles.choice,selectedConst===x.id&&styles.choiceOn]}><Text style={selectedConst===x.id?styles.choiceTextOn:styles.choiceText}>{x.code} · {x.name}</Text></Pressable>)}</View>{c&&<View style={styles.card}><Text style={styles.title}>2. Select booth</Text><TextInput style={styles.input} value={boothSearch} onChangeText={setBoothSearch} placeholder="Search booth no. / name"/>{booths.map((b:any)=><Pressable key={b.id} disabled={busy} onPress={()=>onRequest(b)} style={styles.booth}><Text style={styles.voterName}>Booth {b.boothNo}</Text><Text style={styles.muted}>{b.name||b.address||'Tap to request approval'}</Text></Pressable>)}</View>}<Button label="Refresh approval status" onPress={onRefresh} disabled={!online||busy}/><Button label="Logout" secondary onPress={onLogout}/></ScrollView></SafeAreaView>}
function SurveyModal({voter,onClose,onSave}:any){const [status,setStatus]=useState('Pending'),[notes,setNotes]=useState('');useEffect(()=>{if(voter){setStatus(voter.surveyStatus||'Pending');setNotes(voter.surveyNotes||'')}},[voter]);if(!voter)return null;return <Modal visible animationType="slide" onRequestClose={onClose}><SafeAreaView style={styles.safe}><ScrollView contentContainerStyle={{padding:16}}><View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center'}}><Text style={styles.title}>Voter survey</Text><Button label="Close" small secondary onPress={onClose}/></View><View style={styles.card}><Text style={styles.meta}>Serial {voter.serialNo} · {voter.epicId}</Text><Text style={[styles.voterName,{fontSize:22}]}>{voter.name}</Text><Text style={styles.line}>Relative: {voter.relativeName||'—'}</Text><Text style={styles.line}>House: {voter.houseNo||'—'} · {voter.gender} {voter.age?`· ${voter.age}`:''}</Text></View><View style={styles.card}><Text style={styles.title}>Field-work status</Text><Text style={styles.muted}>These are neutral survey workflow states, not political preference labels.</Text>{['Pending','Visited','Completed','Not Available'].map(x=><Pressable key={x} onPress={()=>setStatus(x)} style={[styles.choice,status===x&&styles.choiceOn]}><Text style={status===x?styles.choiceTextOn:styles.choiceText}>{x}</Text></Pressable>)}<Field label="Notes" value={notes} onChange={setNotes} multiline/><Button label="Save offline" onPress={()=>onSave(voter,status,notes)}/></View></ScrollView></SafeAreaView></Modal>}
function Field({label,value,onChange,secure=false,multiline=false}:any){return <View style={{marginBottom:12}}><Text style={styles.label}>{label}</Text><TextInput style={[styles.input,multiline&&{height:90,textAlignVertical:'top'}]} value={value} onChangeText={onChange} secureTextEntry={secure} multiline={multiline}/></View>}
function Button({label,onPress,secondary=false,small=false,disabled=false}:any){return <Pressable disabled={disabled} onPress={onPress} style={[styles.button,secondary&&styles.buttonSecondary,small&&styles.buttonSmall,disabled&&{opacity:.5}]}><Text style={[styles.buttonText,secondary&&{color:PURPLE}]}>{label}</Text></Pressable>}
function Pill({text,good}:any){return <View style={[styles.pill,{backgroundColor:good?'#EAF8EF':'#FFF5E5'}]}><Text style={{fontSize:11,fontWeight:'900',color:good?GREEN:AMBER}}>{text}</Text></View>}
function StatusPill({status}:any){const good=status==='Completed',warn=status==='Visited';return <View style={[styles.pill,{backgroundColor:good?'#EAF8EF':warn?'#FFF5E5':'#F1F1F4'}]}><Text style={{fontSize:11,fontWeight:'900',color:good?GREEN:warn?AMBER:MUTED}}>{status||'Pending'}</Text></View>}
function Center({children}:any){return <SafeAreaView style={styles.safe}><View style={{flex:1,alignItems:'center',justifyContent:'center',padding:26,gap:12}}>{children}</View></SafeAreaView>}

const styles=StyleSheet.create({safe:{flex:1,backgroundColor:BG},header:{backgroundColor:'#fff',borderBottomWidth:1,borderBottomColor:BORDER,padding:14,flexDirection:'row',alignItems:'center',justifyContent:'space-between',gap:10},appTitle:{fontSize:20,fontWeight:'900',color:TEXT},logo:{fontSize:28,fontWeight:'900',color:PURPLE},title:{fontSize:18,fontWeight:'900',color:TEXT,marginBottom:8},muted:{color:MUTED,lineHeight:20},warn:{color:AMBER,fontWeight:'700'},auth:{padding:20,paddingTop:60,gap:10,maxWidth:520,width:'100%',alignSelf:'center'},card:{backgroundColor:'#fff',borderWidth:1,borderColor:BORDER,borderRadius:16,padding:15},label:{fontSize:12,fontWeight:'800',color:'#504958',marginBottom:6},input:{height:46,borderWidth:1,borderColor:BORDER,borderRadius:10,paddingHorizontal:12,backgroundColor:'#FAF9FC',color:TEXT,flex:1},button:{minHeight:44,backgroundColor:PURPLE,borderRadius:10,paddingHorizontal:14,paddingVertical:10,alignItems:'center',justifyContent:'center',marginTop:6},buttonSecondary:{backgroundColor:'#fff',borderWidth:1,borderColor:PURPLE},buttonSmall:{minHeight:34,paddingVertical:7,marginTop:0},buttonText:{color:'#fff',fontWeight:'900'},pill:{borderRadius:999,paddingHorizontal:9,paddingVertical:5},segment:{flexDirection:'row',backgroundColor:'#F2F0F6',borderRadius:10,padding:3,marginBottom:16},seg:{flex:1,padding:10,alignItems:'center',borderRadius:8},segOn:{backgroundColor:'#fff'},segText:{fontWeight:'800',color:MUTED},segOnText:{fontWeight:'900',color:PURPLE},choice:{padding:12,borderRadius:10,borderWidth:1,borderColor:BORDER,marginTop:8},choiceOn:{borderColor:PURPLE,backgroundColor:'#F4EEFF'},choiceText:{color:TEXT,fontWeight:'700'},choiceTextOn:{color:PURPLE,fontWeight:'900'},booth:{padding:12,borderBottomWidth:1,borderBottomColor:BORDER},syncBar:{backgroundColor:'#fff',paddingHorizontal:12,paddingVertical:8,flexDirection:'row',gap:10,alignItems:'center',borderBottomWidth:1,borderBottomColor:BORDER},syncText:{flex:1,color:MUTED,fontSize:12,fontWeight:'700'},searchRow:{padding:10,flexDirection:'row',alignItems:'center',gap:8},count:{fontWeight:'900',color:PURPLE,minWidth:35,textAlign:'center'},meta:{fontSize:12,color:MUTED,fontWeight:'700'},voterName:{fontSize:17,fontWeight:'900',color:'#4C1D95',marginTop:5},local:{fontSize:13,color:MUTED,marginTop:2},line:{fontSize:13,color:'#3F3947',marginTop:4},bottom:{position:'absolute',left:0,right:0,bottom:0,backgroundColor:'#fff',borderTopWidth:1,borderTopColor:BORDER,padding:8,flexDirection:'row',gap:8,justifyContent:'center'}});
