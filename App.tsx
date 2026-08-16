import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Linking,
  Modal,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';
import { StatusBar } from 'expo-status-bar';

const PURPLE = '#6D28D9';
const PURPLE_DARK = '#4C1D95';
const BG = '#F6F5FA';
const BORDER = '#E7E4EF';
const TEXT = '#17131F';
const MUTED = '#756E82';
const GREEN = '#16A34A';
const RED = '#DC2626';
const AMBER = '#D97706';

const SETTINGS_KEY = '@constituency-manager/settings-v3';

type Tab = 'Dashboard' | 'Voters' | 'Team' | 'Tools' | 'Settings';
type Voter = {
  id: number; serialNo: string; epicId: string; name: string; localName: string;
  relationType: string; relativeName: string; houseNo: string; age: number; gender: string;
  acNo: string; partNo: string; boothNo: string; boothSerialNo: string; ward: string;
  sectionAddress: string; boothAddress: string; phone: string; familyKey: string; assignedTo: string;
  recordStatus: string; dataQuality: string; notes: string; sourcePage: number; family?: Voter[];
};
type Dashboard = { total:number; male:number; female:number; other:number; verified:number; review:number; booths:number; parts:number; team:number };
type TeamMember = { id:number; name:string; phone:string; role:string; area:string; active:boolean };
type PdfRow = Voter & { confidence:number; reviewReason:string };
type PdfJob = { id:string; status:string; progress:number; message:string; rows?:PdfRow[]; pages?:number; ocrPages?:number; extractedRows?:number; cleanRows?:number; reviewRows?:number; activeRows?:number; deletedRows?:number; originalRows?:number; additionRows?:number; summaryMatch?:boolean; expectedSummary?:any; actualSummary?:any; warnings?:string[]; template?:string };

const emptyVoter = (): Omit<Voter,'id'> => ({
  serialNo:'', epicId:'', name:'', localName:'', relationType:'Father', relativeName:'', houseNo:'', age:0, gender:'Other',
  acNo:'', partNo:'', boothNo:'', boothSerialNo:'', ward:'', sectionAddress:'', boothAddress:'', phone:'', familyKey:'', assignedTo:'',
  recordStatus:'Active', dataQuality:'Review', notes:'', sourcePage:0,
});

function isWeb() { return Platform.OS === 'web'; }
function defaultApiUrl() { return isWeb() ? 'http://127.0.0.1:8000' : ''; }

function showMessage(title:string, message:string) {
  if (isWeb()) window.alert(`${title}\n\n${message}`);
  else Alert.alert(title,message);
}

function humanFieldName(loc:any):string {
  const raw=Array.isArray(loc)?loc[loc.length-1]:loc;
  const map:any={epicId:'EPIC ID',serialNo:'Serial No.',acNo:'AC No.',partNo:'Part No.',boothNo:'Booth No.',age:'Age',gender:'Gender',name:'Name',phone:'Mobile',sourcePage:'Source Page'};
  return map[String(raw)] || String(raw || 'Field');
}

function formatApiErrorDetail(detail:any):string {
  if (!detail) return 'The server rejected this request.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((item:any)=>{
      if (typeof item === 'string') return item;
      const field=humanFieldName(item?.loc);
      const msg=String(item?.msg || item?.message || 'Invalid value').replace(/^Value error,\s*/i,'');
      return `${field}: ${msg}`;
    }).join('\n');
  }
  if (typeof detail === 'object') {
    if (detail.message) return String(detail.message);
    try { return JSON.stringify(detail, null, 2); } catch {}
  }
  return String(detail);
}

function normalizeGender(value:any):string {
  const v=String(value||'').trim().toLowerCase();
  if (['male','m','पुरुष'].includes(v)) return 'Male';
  if (['female','f','महिला'].includes(v)) return 'Female';
  return 'Other';
}

function validateManualVoter(editor:any):string[] {
  const errors:string[]=[];
  const name=String(editor?.name||'').trim();
  const epic=String(editor?.epicId||'').replace(/\s+/g,'').toUpperCase();
  const serial=String(editor?.serialNo||'').trim();
  const ageRaw=String(editor?.age ?? '').trim();
  if (name.length < 2) errors.push('Name: enter the voter name.');
  if (!/^[A-Z0-9]{10}$/.test(epic) || !/[A-Z]/.test(epic) || !/\d/.test(epic)) errors.push('EPIC ID: enter a valid 10-character alphanumeric voter ID (example: ABC1234567).');
  if (serial && !/^\d+$/.test(serial)) errors.push('Serial No.: use digits only.');
  if (ageRaw) {
    const age=Number(ageRaw);
    if (!Number.isInteger(age) || age < 18 || age > 120) errors.push('Age: enter a whole number from 18 to 120.');
  }
  for (const [label,key] of [['AC No.','acNo'],['Part No.','partNo'],['Booth No.','boothNo']] as any) {
    const value=String(editor?.[key]||'').trim();
    if (value.length>20) errors.push(`${label}: value is too long.`);
  }
  return errors;
}

function Card({children, style}:any){ return <View style={[styles.card,style]}>{children}</View> }
function Label({children}:any){ return <Text style={styles.label}>{children}</Text> }
function Field({label,value,onChangeText,placeholder,keyboardType='default',multiline=false}:any){
  return <View style={{marginBottom:12}}><Label>{label}</Label><TextInput value={String(value ?? '')} onChangeText={onChangeText} placeholder={placeholder} placeholderTextColor="#AAA3B5" keyboardType={keyboardType} multiline={multiline} style={[styles.input,multiline&&{minHeight:76,textAlignVertical:'top'}]} /></View>
}
function Button({label,onPress,kind='primary',disabled=false,small=false}:any){
  return <Pressable disabled={disabled} onPress={onPress} style={({pressed})=>[styles.button,kind==='secondary'&&styles.buttonSecondary,kind==='danger'&&styles.buttonDanger,kind==='soft'&&styles.buttonSoft,small&&styles.buttonSmall,(pressed||disabled)&&{opacity:.68}]}> <Text style={[styles.buttonText,kind==='secondary'&&styles.buttonTextSecondary,kind==='soft'&&styles.buttonTextSoft]}>{label}</Text></Pressable>
}
function Pill({label,tone='purple'}:any){
  const map:any={purple:['#F1EAFE',PURPLE],green:['#EAF8EF',GREEN],amber:['#FFF5E5',AMBER],red:['#FDECEC',RED],blue:['#EAF3FF','#2563EB'],gray:['#F1F1F4','#5F596A']};
  const [bg,fg]=map[tone]||map.purple; return <View style={[styles.pill,{backgroundColor:bg}]}><Text style={[styles.pillText,{color:fg}]}>{label}</Text></View>
}
function Stat({label,value}:any){ return <View style={styles.statBox}><Text style={styles.statLabel}>{label}</Text><Text style={styles.statValue}>{value}</Text></View> }

export default function App(){
  const { width } = useWindowDimensions();
  const compact = width < 520;
  const veryNarrow = width < 370;
  const [tab,setTab]=useState<Tab>('Dashboard');
  const [apiUrl,setApiUrl]=useState(defaultApiUrl());
  const [apiKey,setApiKey]=useState('');
  const [org,setOrg]=useState('Constituency Management');
  const [connected,setConnected]=useState(false);
  const [backendState,setBackendState]=useState<'connecting'|'online'|'offline'>('connecting');
  const [settingsLoaded,setSettingsLoaded]=useState(false);
  const [loading,setLoading]=useState(false);
  const [dash,setDash]=useState<Dashboard>({total:0,male:0,female:0,other:0,verified:0,review:0,booths:0,parts:0,team:0});
  const [voters,setVoters]=useState<Voter[]>([]);
  const [total,setTotal]=useState(0);
  const [query,setQuery]=useState('');
  const [advancedOpen,setAdvancedOpen]=useState(false);
  const [advanced,setAdvanced]=useState<any>({firstName:'',lastName:'',relativeName:'',acNo:'',partNo:'',serialNo:'',epicId:'',phone:''});
  const [detail,setDetail]=useState<Voter|null>(null);
  const [editor,setEditor]=useState<any>(null);
  const [team,setTeam]=useState<TeamMember[]>([]);
  const [teamEditor,setTeamEditor]=useState<any>(null);
  const [pdfJob,setPdfJob]=useState<PdfJob|null>(null);
  const pollRef=useRef<any>(null);

  useEffect(()=>{(async()=>{
    try{
      const raw=await AsyncStorage.getItem(SETTINGS_KEY);
      if(raw){ try{ const s=JSON.parse(raw); if(s.apiUrl!==undefined)setApiUrl(s.apiUrl); if(s.apiKey)setApiKey(s.apiKey); if(s.org)setOrg(s.org); }catch{} }
    } finally { setSettingsLoaded(true); }
  })()},[]);
  useEffect(()=>()=>{ if(pollRef.current) clearInterval(pollRef.current)},[]);

  const headers=useMemo(()=>({...(apiKey?{'X-App-Key':apiKey}:{}),'Accept':'application/json'}),[apiKey]);
  async function request(path:string,options:any={}){
    if(!apiUrl) throw new Error('Backend URL is not configured');
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(), options.timeoutMs||15000);
    try{
      const res=await fetch(`${apiUrl.replace(/\/$/,'')}${path}`,{...options,signal:controller.signal,headers:{...headers,...(options.headers||{})}});
      const ct=res.headers.get('content-type')||'';
      if(!res.ok){
        let msg=`HTTP ${res.status}`;
        try{ const j=ct.includes('json')?await res.json():null; if(j?.detail!==undefined) msg=formatApiErrorDetail(j.detail); }catch{}
        throw new Error(msg);
      }
      return ct.includes('json')?res.json():res;
    }catch(e:any){
      if(e?.name==='AbortError') throw new Error('Backend timed out. Make sure the API server is running on port 8000.');
      if(String(e?.message||e).includes('Failed to fetch')) throw new Error(`Backend is not reachable at ${apiUrl}. Start the backend, then retry.`);
      throw e;
    }finally{ clearTimeout(timeout); }
  }
  async function testConnection(notify=true){
    setBackendState('connecting');
    setLoading(true);
    try{
      await request('/health',{timeoutMs:5000});
      setConnected(true); setBackendState('online');
      await Promise.all([loadDashboard(),loadVoters(),loadTeam()]);
      if(notify) showMessage('Connected','Backend and database are working.');
      return true;
    }catch(e:any){
      setConnected(false); setBackendState('offline');
      if(notify) showMessage('Connection failed',e.message);
      return false;
    } finally{setLoading(false)}
  }
  async function saveSettings(){ await AsyncStorage.setItem(SETTINGS_KEY,JSON.stringify({apiUrl,apiKey,org})); showMessage('Saved','Settings saved on this device.'); }
  async function loadDashboard(){ try{ setDash(await request('/dashboard')); }catch{} }
  async function loadVoters(params:any={}){
    setLoading(true); try{
      const sp=new URLSearchParams(); if(query)sp.set('query',query); Object.entries(params).forEach(([k,v]:any)=>{if(v)sp.set(k,String(v))}); sp.set('limit','100');
      const data=await request(`/voters?${sp.toString()}`); setVoters(data.items||[]); setTotal(data.total||0);
    }catch(e:any){showMessage('Could not load voters',e.message)}finally{setLoading(false)}
  }
  async function loadTeam(){ try{setTeam(await request('/team'))}catch{} }
  async function openDetail(id:number){ setLoading(true); try{setDetail(await request(`/voters/${id}`))}catch(e:any){showMessage('Could not load voter',e.message)} finally{setLoading(false)} }
  async function saveVoter(){
    try{
      const validationErrors=validateManualVoter(editor);
      if(validationErrors.length){ showMessage('Check voter details',validationErrors.join('\n')); return; }
      const ageText=String(editor.age ?? '').trim();
      const payload={
        ...editor,
        name:String(editor.name||'').trim(),
        epicId:String(editor.epicId||'').replace(/\s+/g,'').toUpperCase(),
        serialNo:String(editor.serialNo||'').trim(),
        age:ageText?Number(ageText):0,
        gender:normalizeGender(editor.gender),
        dataQuality:String(editor.dataQuality||'').trim().toLowerCase()==='verified'?'Verified':'Review',
        sourcePage:Number(editor.sourcePage||0),
      };
      delete payload.id; delete payload.family;
      const path=editor.id?`/voters/${editor.id}`:'/voters'; const method=editor.id?'PUT':'POST';
      await request(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      setEditor(null); await Promise.all([loadVoters(),loadDashboard()]); showMessage('Saved','Voter record saved successfully.');
    }catch(e:any){showMessage('Could not save',formatApiErrorDetail(e?.message||e))}
  }
  async function deleteVoter(v:Voter){
    const ok=isWeb()?window.confirm(`Delete ${v.name}?`):true; if(!ok)return;
    try{await request(`/voters/${v.id}`,{method:'DELETE'});setDetail(null);await Promise.all([loadVoters(),loadDashboard()])}catch(e:any){showMessage('Could not delete',e.message)}
  }
  async function addTeam(){ try{ await request('/team',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(teamEditor)}); setTeamEditor(null); await loadTeam(); await loadDashboard(); }catch(e:any){showMessage('Could not add team member',e.message)} }
  async function deleteTeam(id:number){ try{await request(`/team/${id}`,{method:'DELETE'});await loadTeam();await loadDashboard()}catch(e:any){showMessage('Could not remove member',e.message)} }

  async function pickWebFile(accept:string):Promise<File|null>{
    return await new Promise(resolve=>{
      const input=document.createElement('input'); input.type='file'; input.accept=accept; input.onchange=()=>resolve(input.files?.[0]||null); input.click();
    });
  }
  async function uploadFile(path:string,accept:string,mime:string){
    const form=new FormData();
    if(isWeb()){
      const file=await pickWebFile(accept); if(!file)return null; form.append('file',file,file.name);
    }else{
      const r=await DocumentPicker.getDocumentAsync({type:mime,copyToCacheDirectory:true}); if(r.canceled)return null; const a=r.assets[0]; form.append('file',{uri:a.uri,name:a.name,mimeType:a.mimeType||mime} as any);
    }
    const res=await fetch(`${apiUrl.replace(/\/$/,'')}${path}`,{method:'POST',headers:{...(apiKey?{'X-App-Key':apiKey}:{})},body:form});
    const j=await res.json(); if(!res.ok)throw new Error(j.detail||`HTTP ${res.status}`); return j;
  }
  async function importCsv(){ try{const r=await uploadFile('/import/csv','.csv','text/csv'); if(!r)return; showMessage('CSV imported',`Inserted: ${r.inserted}\nUpdated: ${r.updated}\nRejected: ${r.rejected}`); await Promise.all([loadVoters(),loadDashboard()]);}catch(e:any){showMessage('Import failed',e.message)} }
  async function startPdf(){
    try{setPdfJob({id:'',status:'uploading',progress:0,message:'Uploading PDF'});const r=await uploadFile('/pdf/jobs','.pdf','application/pdf');if(!r){setPdfJob(null);return} const id=r.jobId; setPdfJob({id,status:'queued',progress:0,message:'Queued'});
      if(pollRef.current)clearInterval(pollRef.current);pollRef.current=setInterval(async()=>{try{const j=await request(`/pdf/jobs/${id}`);setPdfJob(j);if(j.status==='done'||j.status==='error'){clearInterval(pollRef.current);pollRef.current=null}}catch{}},1000);
    }catch(e:any){setPdfJob(null);showMessage('PDF conversion failed',e.message)}
  }
  async function commitPdf(){ if(!pdfJob?.id)return; try{const r=await request(`/pdf/jobs/${pdfJob.id}/commit`,{method:'POST'});showMessage('Imported clean rows',`Inserted: ${r.inserted}\nUpdated: ${r.updated}\nSkipped/review: ${r.skipped}`);await Promise.all([loadVoters(),loadDashboard()])}catch(e:any){showMessage('Import failed',e.message)} }
  async function download(path:string,filename:string){
    try{
      const url=`${apiUrl.replace(/\/$/,'')}${path}`;
      if(isWeb()){
        const res=await fetch(url,{headers});if(!res.ok)throw new Error(`HTTP ${res.status}`);const blob=await res.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=filename;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),3000);
      }else{
        const dest=(FileSystem.cacheDirectory||FileSystem.documentDirectory)+filename;const r=await FileSystem.downloadAsync(url,dest,{headers}); if(await Sharing.isAvailableAsync())await Sharing.shareAsync(r.uri);
      }
    }catch(e:any){showMessage('Download failed',e.message)}
  }
  async function openSlip(v:Voter){ const url=`${apiUrl.replace(/\/$/,'')}/voters/${v.id}/slip${apiKey?`?key=${encodeURIComponent(apiKey)}`:''}`; if(isWeb())window.open(url,'_blank'); else Linking.openURL(url); }
  function openMap(v:Voter){ const q=encodeURIComponent(v.boothAddress||v.sectionAddress||v.ward||v.boothNo); if(q)Linking.openURL(`https://www.google.com/maps/search/?api=1&query=${q}`); }

  useEffect(()=>{
    if(!settingsLoaded || !apiUrl) return;
    let cancelled=false;
    (async()=>{
      setBackendState('connecting');
      for(let i=0;i<12;i++){
        try{
          await request('/health',{timeoutMs:2500});
          if(cancelled)return;
          setConnected(true); setBackendState('online');
          await Promise.all([loadDashboard(),loadVoters(),loadTeam()]);
          return;
        }catch{}
        await new Promise(r=>setTimeout(r,750));
      }
      if(!cancelled){ setConnected(false); setBackendState('offline'); }
    })();
    return()=>{cancelled=true};
  },[settingsLoaded]);

  const verifiedPct=dash.total?Math.round(dash.verified/dash.total*100):0;

  return <SafeAreaView style={styles.safe}><StatusBar style="dark" />
    <View style={styles.shell}>
      <View style={[styles.header,compact&&styles.headerCompact]}><View style={styles.headerCopy}><Text numberOfLines={1} style={[styles.appTitle,compact&&styles.appTitleCompact]}>{org}</Text><Text numberOfLines={1} style={styles.appSub}>Election data, organized.</Text></View><View style={styles.headerStatus}><Pill label={backendState==='online'?'ONLINE':backendState==='connecting'?'CONNECTING':'OFFLINE'} tone={backendState==='online'?'green':backendState==='connecting'?'amber':'red'} /></View></View>
      <View style={styles.content}>
        {tab==='Dashboard' && <ScrollView contentContainerStyle={[styles.page,compact&&styles.pageCompact]}>
          <View style={[styles.profileCard,compact&&styles.profileCardCompact]}><View style={[styles.avatar,compact&&styles.avatarCompact]}><Text style={{fontSize:compact?24:30}}>🗳️</Text></View><View style={styles.profileCopy}><Text numberOfLines={2} style={[styles.profileName,compact&&styles.profileNameCompact]}>Constituency Dashboard</Text><Text style={styles.profileSub}>Booths {dash.booths} · Parts {dash.parts} · Team {dash.team}</Text></View></View>
          <View style={[styles.quickGrid,compact&&styles.quickGridCompact]}>
            <Quick icon="📍" label="Booth Map" onPress={()=>setTab('Voters')} />
            <Quick icon="🧭" label="Operations" onPress={()=>setTab('Team')} />
            <Quick icon="📊" label="Voter Reports" onPress={()=>setTab('Tools')} />
          </View>
          <Pressable onPress={()=>setTab('Voters')} style={[styles.searchHero,compact&&styles.searchHeroCompact]}><Text style={{color:MUTED}}>Search voter name or EPIC</Text><Text style={{color:PURPLE,fontSize:20}}>⌕</Text></Pressable>
          <Card><Text style={styles.sectionTitle}>Voter Details</Text><View style={styles.statsRow}><Stat label="Total Voters" value={dash.total.toLocaleString()} /><Stat label="Male" value={dash.male.toLocaleString()} /><Stat label="Female" value={dash.female.toLocaleString()} /><Stat label="Other" value={dash.other.toLocaleString()} /></View></Card>
          <Card><Text style={styles.sectionTitle}>Data Quality</Text><View style={[styles.qualityWrap,compact&&styles.qualityWrapCompact,veryNarrow&&styles.qualityWrapNarrow]}><View style={[styles.donut,compact&&styles.donutCompact,veryNarrow&&styles.donutNarrow]}><View style={[styles.donutInner,compact&&styles.donutInnerCompact]}><Text style={{fontWeight:'800',fontSize:22,color:PURPLE}}>{verifiedPct}%</Text><Text style={{fontSize:11,color:MUTED}}>verified</Text></View></View><View style={{flex:1,gap:10}}><QualityLine dot="#BBF7D0" label="Verified" value={dash.verified}/><QualityLine dot="#FDE68A" label="Needs review" value={dash.review}/><QualityLine dot="#BFDBFE" label="Booths" value={dash.booths}/><QualityLine dot="#E9D5FF" label="Parts" value={dash.parts}/></View></View></Card>
        </ScrollView>}

        {tab==='Voters' && <View style={{flex:1}}>
          <View style={[styles.pageTop,compact&&styles.pageTopCompact]}><Text style={[styles.pageTitle,compact&&styles.pageTitleCompact]}>Voter List</Text><Button label="+ Add voter" small onPress={()=>setEditor({...emptyVoter()})}/></View>
          <View style={[styles.searchRow,compact&&styles.searchRowCompact]}><TextInput value={query} onChangeText={setQuery} onSubmitEditing={()=>loadVoters()} placeholder="Search Voter Name or EPIC" placeholderTextColor="#A59EAF" style={[styles.input,{flex:1,marginBottom:0}]} /><Pressable style={styles.iconBtn} onPress={()=>loadVoters()}><Text style={{fontSize:20,color:PURPLE}}>⌕</Text></Pressable><Pressable style={styles.iconBtn} onPress={()=>setAdvancedOpen(true)}><Text style={{fontSize:19,color:PURPLE}}>☷</Text></Pressable></View>
          <Text style={styles.showing}>Showing {voters.length}/{total}</Text>
          {loading?<ActivityIndicator color={PURPLE} style={{marginTop:30}}/>:<FlatList data={voters} keyExtractor={x=>String(x.id)} contentContainerStyle={{padding:compact?10:14,paddingBottom:110,gap:10,maxWidth:1080,width:'100%',alignSelf:'center'}} renderItem={({item})=><Pressable onPress={()=>openDetail(item.id)}><Card style={{margin:0}}><View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start',gap:8}}><Text style={[styles.voterMeta,{flex:1,minWidth:0}]}>AC: {item.acNo||'—'} · Part {item.partNo||'—'} · Serial {item.serialNo||'—'}</Text><Pill label={item.dataQuality||'Review'} tone={item.dataQuality==='Verified'?'green':'amber'} /></View><Text style={styles.voterName}>{item.name}</Text>{item.localName?<Text style={styles.localName}>{item.localName}</Text>:null}<Text style={styles.voterLine}>Relative: {item.relativeName||'—'}</Text><View style={{flexDirection:'row',gap:18,marginTop:6}}><Text style={styles.voterLine}>Booth: {item.boothNo||'—'}</Text><Text style={styles.voterLine}>{item.gender} {item.age?`(${item.age})`:''}</Text></View><View style={{marginTop:10,alignItems:'flex-end'}}><Text style={styles.detailsLink}>Details ›</Text></View></Card></Pressable>} ListEmptyComponent={<Empty title="No voter records" text="Import a CSV/PDF or add a voter manually."/>} />}
        </View>}

        {tab==='Team' && <ScrollView contentContainerStyle={[styles.page,compact&&styles.pageCompact]}><View style={[styles.pageTop,compact&&styles.pageTopCompact]}><View style={{flex:1,minWidth:0}}><Text style={[styles.pageTitle,compact&&styles.pageTitleCompact]}>My Team</Text><Text style={styles.pageDesc}>Manage field staff and booth assignments.</Text></View><Button label="+ Add" small onPress={()=>setTeamEditor({name:'',phone:'',role:'Field Staff',area:'',active:true})}/></View>{team.map(m=><Card key={m.id}><View style={{flexDirection:'row',justifyContent:'space-between',gap:12}}><View style={{flex:1}}><Text style={styles.voterName}>{m.name}</Text><Text style={styles.voterLine}>{m.role} · {m.area||'No area assigned'}</Text><Text style={styles.voterLine}>{m.phone||'No phone'}</Text></View><Button label="Remove" kind="danger" small onPress={()=>deleteTeam(m.id)} /></View></Card>)}{!team.length&&<Empty title="No team members" text="Add representatives or field staff and assign areas."/>}</ScrollView>}

        {tab==='Tools' && <ScrollView contentContainerStyle={[styles.page,compact&&styles.pageCompact]}><Text style={[styles.pageTitle,compact&&styles.pageTitleCompact]}>Reports, imports & backup</Text><Text style={styles.pageDesc}>Use reviewed source data and keep uncertain OCR rows separate.</Text>
          <Card><View style={styles.cardHead}><View><Text style={styles.sectionTitle}>Voter PDF → Excel</Text><Text style={styles.cardDesc}>Optimized for scanned Hindi electoral rolls with 3-column voter cards. Card-by-card OCR prevents neighbouring records from mixing.</Text></View><Pill label="XLSX" tone="green"/></View><Button label="Choose voter PDF" onPress={startPdf}/>{pdfJob&&<View style={{marginTop:12}}><Text style={styles.statusText}>{pdfJob.message}</Text><View style={styles.progressTrack}><View style={[styles.progressFill,{width:`${pdfJob.progress||0}%`}]} /></View>{pdfJob.status==='done'&&<><Text style={styles.cardDesc}>Template: {pdfJob.template||'Auto-detect'} · Pages: {pdfJob.pages||0} · Extracted cards: {pdfJob.extractedRows ?? pdfJob.rows?.length ?? 0}</Text><Text style={styles.cardDesc}>Active: {pdfJob.activeRows ?? 0} · Deleted: {pdfJob.deletedRows ?? 0} · Original roll: {pdfJob.originalRows ?? 0} · Additions: {pdfJob.additionRows ?? 0}</Text><Text style={styles.cardDesc}>Verified active: {pdfJob.cleanRows||0} · Review: {pdfJob.reviewRows||0}</Text>{pdfJob.summaryMatch===true?<Text style={{color:'#15803D',fontWeight:'800',marginTop:6}}>✓ Extracted totals match the PDF summary.</Text>:pdfJob.summaryMatch===false?<Text style={{color:'#B45309',fontWeight:'800',marginTop:6}}>⚠ Extracted totals do not exactly match the PDF summary. Open the Summary/Review sheets before importing.</Text>:null}{pdfJob.warnings?.length?<Text style={{color:'#B45309',marginTop:6}}>{pdfJob.warnings.join(' · ')}</Text>:null}{(pdfJob.cleanRows||0)===0&&(pdfJob.reviewRows||0)>0?<Text style={{color:'#B45309',fontWeight:'700',marginTop:6}}>Rows were extracted but need review. Excel still contains every detected record.</Text>:null}{(pdfJob.extractedRows ?? pdfJob.rows?.length ?? 0)===0?<Text style={{color:RED,fontWeight:'700',marginTop:6}}>No voter rows were detected. This PDF layout needs extractor tuning before it can be imported safely.</Text>:null}<View style={{flexDirection:'row',gap:8,flexWrap:'wrap',marginTop:8}}><Button small label="Download Excel" onPress={()=>download(`/pdf/jobs/${pdfJob.id}/xlsx`,'voter-extraction.xlsx')}/><Button small kind="secondary" disabled={(pdfJob.cleanRows||0)===0} label={(pdfJob.cleanRows||0)>0?"Import clean rows":"No clean rows to import"} onPress={commitPdf}/></View></>}{pdfJob.status==='error'&&<Text style={{color:RED,fontWeight:'700',marginTop:8}}>{pdfJob.message}</Text>}</View>}</Card>
          <Card><Text style={styles.sectionTitle}>Import voter CSV</Text><Text style={styles.cardDesc}>Upserts by EPIC ID. Invalid rows are rejected and reported.</Text><Button label="Import CSV" onPress={importCsv}/></Card>
          <Card><Text style={styles.sectionTitle}>Export voter database</Text><Text style={styles.cardDesc}>UTF-8 CSV or formatted XLSX.</Text><View style={{flexDirection:'row',gap:8,flexWrap:'wrap'}}><Button small label="Export CSV" onPress={()=>download('/export/voters.csv','voters.csv')}/><Button small kind="secondary" label="Export XLSX" onPress={()=>download('/export/voters.xlsx','voters.xlsx')}/></View></Card>
          <Card><Text style={styles.sectionTitle}>Operational safety</Text><Text style={styles.cardDesc}>Use the Review Queue before importing OCR data. Keep database backups and verify election-roll details against official source documents.</Text></Card>
        </ScrollView>}

        {tab==='Settings' && <ScrollView contentContainerStyle={[styles.page,compact&&styles.pageCompact]}><Text style={[styles.pageTitle,compact&&styles.pageTitleCompact]}>Settings</Text><Card><Field label="Organization / constituency name" value={org} onChangeText={setOrg}/><Field label="Backend URL" value={apiUrl} onChangeText={setApiUrl} placeholder="http://127.0.0.1:8000"/><Field label="API key (optional)" value={apiKey} onChangeText={setApiKey} placeholder="Only if APP_API_KEY is enabled"/><View style={{flexDirection:'row',gap:8,flexWrap:'wrap'}}><Button small label="Save settings" onPress={saveSettings}/><Button small kind="secondary" label={loading?'Testing…':'Test connection'} onPress={()=>testConnection(true)}/></View><Text style={styles.helpText}>Mac/Windows browser: http://127.0.0.1:8000. Physical phone: use your computer's LAN IP, e.g. http://192.168.1.5:8000.</Text></Card></ScrollView>}
      </View>
      <View style={[styles.nav,compact&&styles.navCompact]}>{(['Dashboard','Voters','Tools','Team','Settings'] as Tab[]).map(t=><Pressable key={t} onPress={()=>{setTab(t);if(t==='Dashboard')loadDashboard();if(t==='Voters')loadVoters();if(t==='Team')loadTeam()}} style={[styles.navItem,compact&&styles.navItemCompact]}><Text style={[styles.navIcon,tab===t&&{color:PURPLE}]}>{t==='Dashboard'?'▦':t==='Voters'?'☷':t==='Tools'?'▤':t==='Team'?'◉':'⚙'}</Text><Text style={[styles.navText,tab===t&&styles.navTextActive]}>{t}</Text></Pressable>)}</View>
    </View>

    <Modal visible={advancedOpen} transparent animationType="fade" onRequestClose={()=>setAdvancedOpen(false)}><View style={styles.modalShade}><View style={[styles.modalCard,compact&&styles.modalCardCompact]}><Text style={styles.modalTitle}>Advanced Search</Text><ScrollView><Field label="First name" value={advanced.firstName} onChangeText={(v:string)=>setAdvanced({...advanced,firstName:v})}/><Field label="Last name" value={advanced.lastName} onChangeText={(v:string)=>setAdvanced({...advanced,lastName:v})}/><Field label="Relative name" value={advanced.relativeName} onChangeText={(v:string)=>setAdvanced({...advanced,relativeName:v})}/><View style={[styles.advancedTriplet,compact&&styles.advancedTripletCompact]}><View style={styles.advancedField}><Field label="AC" value={advanced.acNo} onChangeText={(v:string)=>setAdvanced({...advanced,acNo:v})}/></View><View style={styles.advancedField}><Field label="Part No." value={advanced.partNo} onChangeText={(v:string)=>setAdvanced({...advanced,partNo:v})}/></View><View style={styles.advancedField}><Field label="Serial" value={advanced.serialNo} onChangeText={(v:string)=>setAdvanced({...advanced,serialNo:v})}/></View></View><Field label="EPIC" value={advanced.epicId} onChangeText={(v:string)=>setAdvanced({...advanced,epicId:v})}/><Field label="Mobile" value={advanced.phone} onChangeText={(v:string)=>setAdvanced({...advanced,phone:v})}/></ScrollView><View style={styles.modalActions}><Button kind="secondary" label="Cancel" onPress={()=>setAdvancedOpen(false)}/><Button label="Search" onPress={()=>{setAdvancedOpen(false);loadVoters(advanced)}}/></View></View></View></Modal>

    <Modal visible={!!detail} animationType="slide" onRequestClose={()=>setDetail(null)}><SafeAreaView style={styles.safe}>{detail&&<ScrollView contentContainerStyle={[styles.page,compact&&styles.pageCompact]}><View style={[styles.pageTop,compact&&styles.pageTopCompact]}><Button small kind="secondary" label="‹ Back" onPress={()=>setDetail(null)}/><Text style={[styles.pageTitle,compact&&styles.pageTitleCompact]}>Voter Details</Text><Button small label="Edit" onPress={()=>setEditor({...detail})}/></View><Card><Text style={styles.voterMeta}>AC {detail.acNo||'—'} · Part {detail.partNo||'—'} · Serial {detail.serialNo||'—'}</Text><Text style={styles.detailName}>{detail.name}</Text>{detail.localName?<Text style={styles.localName}>{detail.localName}</Text>:null}<Text style={styles.detailLine}>Relative: {detail.relationType} {detail.relativeName||'—'}</Text><View style={styles.divider}/><Two a="EPIC ID" av={detail.epicId} b="Gender / Age" bv={`${detail.gender}${detail.age?` (${detail.age})`:''}`}/><Two a="House" av={detail.houseNo||'—'} b="Booth" bv={detail.boothNo||'—'}/><Two a="Booth serial" av={detail.boothSerialNo||'—'} b="Ward" bv={detail.ward||'—'}/><Text style={styles.detailLabel}>Section address</Text><Text style={styles.detailValue}>{detail.sectionAddress||'—'}</Text><Text style={styles.detailLabel}>Booth address</Text><Text style={styles.detailValue}>{detail.boothAddress||'—'}</Text></Card><Card><Text style={styles.sectionTitle}>Record Status</Text><View style={{flexDirection:'row',gap:8,flexWrap:'wrap'}}><Pill label={detail.dataQuality} tone={detail.dataQuality==='Verified'?'green':'amber'}/><Pill label={detail.recordStatus} tone="blue"/></View><Text style={styles.cardDesc}>Status colors are for data quality/workflow only, not political preference.</Text></Card><Card><Text style={styles.sectionTitle}>Family</Text>{detail.family?.length?detail.family.map(f=><Pressable key={f.id} onPress={()=>openDetail(f.id)}><Text style={styles.familyRow}>• {f.name} · {f.epicId}</Text></Pressable>):<Text style={styles.cardDesc}>No linked family records. Use the same Family Key to group household records.</Text>}</Card><Card><Text style={styles.sectionTitle}>Contact & assignment</Text><Text style={styles.detailValue}>{detail.phone||'No phone saved'}</Text><Text style={styles.detailValue}>Assigned to: {detail.assignedTo||'—'}</Text></Card><View style={{flexDirection:'row',gap:8,flexWrap:'wrap'}}><Button small label="Print voter info slip" onPress={()=>openSlip(detail)}/><Button small kind="secondary" label="Open booth map" onPress={()=>openMap(detail)}/><Button small kind="danger" label="Delete" onPress={()=>deleteVoter(detail)}/></View></ScrollView>}</SafeAreaView></Modal>

    <Modal visible={!!editor} animationType="slide" onRequestClose={()=>setEditor(null)}><SafeAreaView style={styles.safe}>{editor&&<ScrollView contentContainerStyle={[styles.page,compact&&styles.pageCompact]}><View style={[styles.pageTop,compact&&styles.pageTopCompact]}><Button small kind="secondary" label="Cancel" onPress={()=>setEditor(null)}/><Text style={[styles.pageTitle,compact&&styles.pageTitleCompact]}>{editor.id?'Edit voter':'Add voter'}</Text><Button small label="Save" onPress={saveVoter}/></View><Card><Field label="Name" value={editor.name} onChangeText={(v:string)=>setEditor({...editor,name:v})}/><Field label="Local/Hindi name" value={editor.localName} onChangeText={(v:string)=>setEditor({...editor,localName:v})}/><Field label="EPIC ID" value={editor.epicId} onChangeText={(v:string)=>setEditor({...editor,epicId:v.toUpperCase()})}/><View style={styles.formGrid}><Field label="Serial No." value={editor.serialNo} onChangeText={(v:string)=>setEditor({...editor,serialNo:v})}/><Field label="AC No." value={editor.acNo} onChangeText={(v:string)=>setEditor({...editor,acNo:v})}/><Field label="Part No." value={editor.partNo} onChangeText={(v:string)=>setEditor({...editor,partNo:v})}/><Field label="Booth No." value={editor.boothNo} onChangeText={(v:string)=>setEditor({...editor,boothNo:v})}/></View><Field label="Relation type" value={editor.relationType} onChangeText={(v:string)=>setEditor({...editor,relationType:v})}/><Field label="Relative name" value={editor.relativeName} onChangeText={(v:string)=>setEditor({...editor,relativeName:v})}/><View style={styles.formGrid}><Field label="House No." value={editor.houseNo} onChangeText={(v:string)=>setEditor({...editor,houseNo:v})}/><Field label="Age" value={editor.age} keyboardType="numeric" onChangeText={(v:string)=>setEditor({...editor,age:v})}/><Field label="Gender" value={editor.gender} onChangeText={(v:string)=>setEditor({...editor,gender:v})}/><Field label="Booth serial" value={editor.boothSerialNo} onChangeText={(v:string)=>setEditor({...editor,boothSerialNo:v})}/></View><Field label="Ward / area" value={editor.ward} onChangeText={(v:string)=>setEditor({...editor,ward:v})}/><Field label="Section address" value={editor.sectionAddress} onChangeText={(v:string)=>setEditor({...editor,sectionAddress:v})} multiline/><Field label="Booth address" value={editor.boothAddress} onChangeText={(v:string)=>setEditor({...editor,boothAddress:v})} multiline/><Field label="Mobile" value={editor.phone} onChangeText={(v:string)=>setEditor({...editor,phone:v})}/><Field label="Family Key" value={editor.familyKey} onChangeText={(v:string)=>setEditor({...editor,familyKey:v})}/><Field label="Assigned to" value={editor.assignedTo} onChangeText={(v:string)=>setEditor({...editor,assignedTo:v})}/><Field label="Data quality (Verified / Review)" value={editor.dataQuality} onChangeText={(v:string)=>setEditor({...editor,dataQuality:v})}/><Field label="Notes" value={editor.notes} onChangeText={(v:string)=>setEditor({...editor,notes:v})} multiline/></Card></ScrollView>}</SafeAreaView></Modal>

    <Modal visible={!!teamEditor} transparent animationType="fade" onRequestClose={()=>setTeamEditor(null)}><View style={styles.modalShade}>{teamEditor&&<View style={[styles.modalCard,compact&&styles.modalCardCompact]}><Text style={styles.modalTitle}>Add Team Member</Text><Field label="Name" value={teamEditor.name} onChangeText={(v:string)=>setTeamEditor({...teamEditor,name:v})}/><Field label="Phone" value={teamEditor.phone} onChangeText={(v:string)=>setTeamEditor({...teamEditor,phone:v})}/><Field label="Role" value={teamEditor.role} onChangeText={(v:string)=>setTeamEditor({...teamEditor,role:v})}/><Field label="Area / booth" value={teamEditor.area} onChangeText={(v:string)=>setTeamEditor({...teamEditor,area:v})}/><View style={styles.modalActions}><Button kind="secondary" label="Cancel" onPress={()=>setTeamEditor(null)}/><Button label="Add" onPress={addTeam}/></View></View>}</View></Modal>
  </SafeAreaView>
}

function Quick({icon,label,onPress}:any){const {width}=useWindowDimensions();const compact=width<520;return <Pressable onPress={onPress} style={[styles.quick,compact&&styles.quickCompact]}><View style={[styles.quickIcon,compact&&styles.quickIconCompact]}><Text style={{fontSize:compact?25:30}}>{icon}</Text></View><Text numberOfLines={2} style={[styles.quickLabel,compact&&styles.quickLabelCompact]}>{label}</Text></Pressable>}
function QualityLine({dot,label,value}:any){return <View style={{flexDirection:'row',alignItems:'center',gap:8}}><View style={{width:12,height:12,borderRadius:6,backgroundColor:dot}}/><Text style={{flex:1,color:MUTED}}>{label}</Text><Text style={{fontWeight:'800',color:TEXT}}>{value}</Text></View>}
function Empty({title,text}:any){return <View style={{padding:36,alignItems:'center'}}><Text style={{fontWeight:'800',fontSize:18,color:TEXT}}>{title}</Text><Text style={{marginTop:6,color:MUTED,textAlign:'center'}}>{text}</Text></View>}
function Two({a,av,b,bv}:any){const {width}=useWindowDimensions();const compact=width<420;return <View style={{flexDirection:compact?'column':'row',gap:compact?2:12,marginBottom:10}}><View style={{flex:1,minWidth:0}}><Text style={styles.detailLabel}>{a}</Text><Text style={styles.detailValue}>{av}</Text></View><View style={{flex:1,minWidth:0}}><Text style={styles.detailLabel}>{b}</Text><Text style={styles.detailValue}>{bv}</Text></View></View>}

const styles=StyleSheet.create({
  safe:{flex:1,backgroundColor:'#fff'}, shell:{flex:1,backgroundColor:BG,overflow:'hidden'}, header:{minHeight:74,backgroundColor:'#fff',borderBottomWidth:1,borderBottomColor:BORDER,paddingHorizontal:18,paddingVertical:10,flexDirection:'row',alignItems:'center',justifyContent:'space-between',gap:10},headerCompact:{minHeight:68,paddingHorizontal:12,paddingVertical:8},headerCopy:{flex:1,minWidth:0},headerStatus:{flexShrink:0,alignItems:'flex-end'},appTitle:{fontSize:20,fontWeight:'900',color:TEXT,flexShrink:1},appTitleCompact:{fontSize:17},appSub:{fontSize:12,color:MUTED,marginTop:2},content:{flex:1,minWidth:0},page:{padding:16,paddingBottom:120,maxWidth:1080,width:'100%',alignSelf:'center'},pageCompact:{width:'auto',alignSelf:'stretch',paddingHorizontal:12,paddingTop:12,paddingBottom:110},pageTop:{padding:14,paddingBottom:8,flexDirection:'row',alignItems:'center',justifyContent:'space-between',gap:10},pageTopCompact:{paddingHorizontal:12,flexWrap:'wrap'},pageTitle:{fontSize:24,fontWeight:'900',color:TEXT},pageTitleCompact:{fontSize:20},pageDesc:{color:MUTED,marginTop:4,marginBottom:12},
  profileCard:{backgroundColor:'#fff',borderWidth:1,borderColor:BORDER,borderRadius:16,padding:14,flexDirection:'row',alignItems:'center',gap:14},profileCardCompact:{padding:12,gap:10},profileCopy:{flex:1,minWidth:0},avatar:{width:66,height:66,borderRadius:33,backgroundColor:'#F0E7FF',alignItems:'center',justifyContent:'center'},avatarCompact:{width:52,height:52,borderRadius:26},profileName:{fontSize:21,fontWeight:'900',color:TEXT},profileNameCompact:{fontSize:18},profileSub:{color:MUTED,marginTop:4,flexShrink:1},quickGrid:{flexDirection:'row',justifyContent:'space-around',marginVertical:14,gap:8},quickGridCompact:{gap:4,marginVertical:10},quick:{flex:1,minWidth:0,alignItems:'center',paddingVertical:8},quickCompact:{paddingHorizontal:2},quickIcon:{width:72,height:54,borderRadius:18,backgroundColor:'#F6F1FF',alignItems:'center',justifyContent:'center'},quickIconCompact:{width:58,height:48,borderRadius:15},quickLabel:{fontWeight:'800',fontSize:13,marginTop:6,color:TEXT,textAlign:'center'},quickLabelCompact:{fontSize:11,lineHeight:14},searchHero:{backgroundColor:'#fff',borderWidth:1.5,borderColor:PURPLE,borderRadius:13,padding:14,flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:14,minWidth:0},searchHeroCompact:{paddingHorizontal:12,paddingVertical:12},
  card:{backgroundColor:'#fff',borderWidth:1,borderColor:BORDER,borderRadius:16,padding:16,marginBottom:14,shadowColor:'#000',shadowOpacity:.04,shadowRadius:7,shadowOffset:{width:0,height:2}},cardHead:{flexDirection:'row',justifyContent:'space-between',gap:12,alignItems:'flex-start'},sectionTitle:{fontSize:17,fontWeight:'900',color:TEXT,marginBottom:10},cardDesc:{color:MUTED,lineHeight:20,marginBottom:12},statsRow:{flexDirection:'row',gap:8,flexWrap:'wrap'},statBox:{flexGrow:1,flexBasis:150,minWidth:0,backgroundColor:'#FBFAFD',borderRadius:12,padding:12},statLabel:{fontSize:12,fontWeight:'700',color:MUTED},statValue:{fontSize:21,fontWeight:'900',color:PURPLE,marginTop:14},qualityWrap:{flexDirection:'row',alignItems:'center',gap:24},qualityWrapCompact:{gap:14,alignItems:'center'},qualityWrapNarrow:{flexDirection:'column',alignItems:'stretch'},donut:{width:130,height:130,borderRadius:65,borderWidth:22,borderColor:PURPLE,alignItems:'center',justifyContent:'center',flexShrink:0},donutCompact:{width:104,height:104,borderRadius:52,borderWidth:18},donutNarrow:{alignSelf:'center'},donutInner:{width:72,height:72,borderRadius:36,backgroundColor:'#fff',alignItems:'center',justifyContent:'center'},donutInnerCompact:{width:60,height:60,borderRadius:30},
  searchRow:{paddingHorizontal:14,flexDirection:'row',gap:8,alignItems:'center'},searchRowCompact:{paddingHorizontal:10,gap:6},iconBtn:{width:46,height:46,borderRadius:12,borderWidth:1,borderColor:BORDER,backgroundColor:'#fff',alignItems:'center',justifyContent:'center'},showing:{paddingHorizontal:16,paddingTop:10,color:TEXT,fontWeight:'800'},input:{height:46,borderWidth:1,borderColor:BORDER,borderRadius:10,paddingHorizontal:13,backgroundColor:'#F9F8FB',color:TEXT,fontSize:15},label:{fontSize:12,fontWeight:'800',color:'#504958',marginBottom:6},button:{minHeight:44,borderRadius:10,paddingHorizontal:16,paddingVertical:11,backgroundColor:PURPLE,alignItems:'center',justifyContent:'center'},buttonSmall:{minHeight:36,paddingVertical:8,paddingHorizontal:12},buttonSecondary:{backgroundColor:'#fff',borderWidth:1,borderColor:PURPLE},buttonDanger:{backgroundColor:RED},buttonSoft:{backgroundColor:'#F3E8FF'},buttonText:{color:'#fff',fontWeight:'900'},buttonTextSecondary:{color:PURPLE},buttonTextSoft:{color:PURPLE},pill:{borderRadius:999,paddingHorizontal:9,paddingVertical:5,alignSelf:'flex-start'},pillText:{fontSize:11,fontWeight:'900'},
  voterMeta:{fontSize:12,color:MUTED,fontWeight:'700'},voterName:{fontSize:17,fontWeight:'900',color:PURPLE_DARK,marginTop:5},localName:{fontSize:13,color:'#6A6372',marginTop:2},voterLine:{fontSize:13,color:'#3F3947',marginTop:4},detailsLink:{backgroundColor:PURPLE,color:'#fff',fontWeight:'900',paddingHorizontal:13,paddingVertical:7,borderRadius:8,overflow:'hidden'},
  nav:{height:72,backgroundColor:'#fff',borderTopWidth:1,borderTopColor:BORDER,flexDirection:'row',paddingBottom:isWeb()?0:6},navCompact:{height:66},navItem:{flex:1,minWidth:0,alignItems:'center',justifyContent:'center',gap:2},navItemCompact:{paddingHorizontal:1},navIcon:{fontSize:22,color:'#8E879A'},navText:{fontSize:10,fontWeight:'700',color:'#8E879A'},navTextActive:{color:PURPLE,fontWeight:'900'},
  modalShade:{flex:1,backgroundColor:'rgba(18,13,27,.54)',alignItems:'center',justifyContent:'center',padding:20},modalCard:{backgroundColor:'#fff',borderRadius:16,padding:18,maxHeight:'88%',width:'100%',maxWidth:520},modalCardCompact:{padding:14,maxHeight:'92%'},modalTitle:{fontSize:20,fontWeight:'900',textAlign:'center',marginBottom:16,color:TEXT},modalActions:{flexDirection:'row',justifyContent:'flex-end',gap:8,marginTop:6,flexWrap:'wrap'},advancedTriplet:{flexDirection:'row',gap:8},advancedTripletCompact:{flexDirection:'column',gap:0},advancedField:{flex:1,minWidth:0},
  detailName:{fontSize:23,fontWeight:'900',color:PURPLE,marginTop:8},detailLine:{marginTop:8,fontWeight:'700',color:'#383240'},divider:{height:1,backgroundColor:BORDER,marginVertical:14},detailLabel:{fontSize:11,fontWeight:'800',color:MUTED,marginBottom:3},detailValue:{fontSize:14,fontWeight:'700',color:TEXT,marginBottom:10},familyRow:{paddingVertical:8,borderBottomWidth:1,borderBottomColor:BORDER,color:PURPLE,fontWeight:'800'},formGrid:{display:'flex',flexDirection:'column'},helpText:{marginTop:14,color:MUTED,lineHeight:19},statusText:{fontWeight:'800',color:TEXT},progressTrack:{height:8,borderRadius:8,backgroundColor:'#EFEAF5',overflow:'hidden',marginVertical:8},progressFill:{height:'100%',backgroundColor:PURPLE},
});
