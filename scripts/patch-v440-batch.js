const fs = require('fs');

const target = process.argv[2] || 'App.tsx';
let s = fs.readFileSync(target, 'utf8');

const importNeedle = "import * as TaskManager from 'expo-task-manager';";
if (!s.includes(importNeedle)) throw new Error('TaskManager import marker not found');
s = s.replace(importNeedle, `${importNeedle}\nimport * as DocumentPicker from 'expo-document-picker';\nimport * as FileSystem from 'expo-file-system/legacy';\nimport * as Sharing from 'expo-sharing';`);

const typeNeedle = "type Perf={designation:string;recordsUpdated:number;totalUpdates:number;housesVisited:number;activeDays:number;volunteersReferred:number;referralsApproved:number;gpsPoints90d:number;trackedDistanceKm90d:number;lastActivityAt?:string|null};";
if (!s.includes(typeNeedle)) throw new Error('Perf type marker not found');
s = s.replace(typeNeedle, `${typeNeedle}\ntype PdfJob={jobId:string;batchId:string;filename:string;status:string;progress:number;message:string;downloadReady:boolean;extractedRows:number;cleanRows:number;reviewRows:number};`);

const adminReturn = "if(session.user.role==='admin')return <AdminHome user={session.user} online={online} onLogout={logout}/>;";
if (!s.includes(adminReturn)) throw new Error('AdminHome return marker not found');
s = s.replace(adminReturn, "if(session.user.role==='admin')return <AdminHome user={session.user} online={online} token={token} onLogout={logout}/>;");

const start = s.indexOf('function AdminHome(');
const end = s.indexOf('\nfunction AssignmentHome(', start);
if (start < 0 || end < 0) throw new Error('AdminHome component markers not found');

const replacement = String.raw`function AdminHome({user,online,token,onLogout}:any){
  const [picked,setPicked]=useState<any[]>([]),[pdfBusy,setPdfBusy]=useState(false),[batchId,setBatchId]=useState(''),[pdfJobs,setPdfJobs]=useState<PdfJob[]>([]);
  const active=pdfJobs.some(j=>j.status==='queued'||j.status==='processing');
  const done=pdfJobs.length>0&&pdfJobs.every(j=>j.status==='done');
  const failed=pdfJobs.filter(j=>j.status==='error').length;

  async function pickPdfs(){
    try{
      const out=await DocumentPicker.getDocumentAsync({type:'*/*',multiple:true,copyToCacheDirectory:true});
      if(out.canceled)return;
      const files=(out.assets||[]).filter((a:any)=>String(a.name||'').toLowerCase().endsWith('.pdf')).slice(0,50);
      if(!files.length)throw new Error('Choose PDF files only.');
      setPicked(files);
      if((out.assets||[]).length>50)msg('Batch limited','Only the first 50 PDFs were selected.');
    }catch(e:any){msg('Could not select PDFs',e.message)}
  }

  async function uploadOne(asset:any,id:string){
    if(Platform.OS==='web'){
      const fd=new FormData();
      if(asset.file)fd.append('file',asset.file,asset.name||'roll.pdf');
      else throw new Error('Browser file data is unavailable.');
      fd.append('batchId',id);
      const r=await fetch(API+'/admin/pdf/bulk/single',{method:'POST',headers:{Authorization:\`Bearer \${token}\`,Accept:'application/json'},body:fd});
      const body=await r.json().catch(()=>({detail:'Upload failed'}));
      if(!r.ok)throw new Error(body.detail||\`HTTP \${r.status}\`);
      return body;
    }
    const res=await FileSystem.uploadAsync(API+'/admin/pdf/bulk/single',asset.uri,{
      httpMethod:'POST',uploadType:FileSystem.FileSystemUploadType.MULTIPART,fieldName:'file',mimeType:'application/pdf',
      parameters:{batchId:id},headers:{Authorization:\`Bearer \${token}\`,Accept:'application/json'}
    });
    let body:any={};try{body=JSON.parse(res.body||'{}')}catch{}
    if(res.status<200||res.status>=300)throw new Error(body.detail||\`Upload failed (HTTP \${res.status})\`);
    return body;
  }

  async function loadBatch(id=batchId,silent=false){
    if(!id)return;
    try{const out=await api('/admin/pdf/batches/'+encodeURIComponent(id),token,{timeoutMs:90000});setPdfJobs(out.jobs||[])}catch(e:any){if(!silent)msg('Could not refresh batch',e.message)}
  }

  async function startBatch(){
    if(!picked.length)return;
    if(!online){msg('Internet required','Connect to the internet before uploading constituency PDFs.');return}
    setPdfBusy(true);
    const id='mobile-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,7);
    setBatchId(id);setPdfJobs([]);
    try{
      await api('/admin/pdf/config',token,{timeoutMs:90000});
      const errors:string[]=[];
      for(let i=0;i<picked.length;i+=3){
        const group=picked.slice(i,i+3);
        const results=await Promise.all(group.map(async(a:any)=>{try{return await uploadOne(a,id)}catch(e:any){errors.push(\`\${a.name}: \${e.message}\`);return null}}));
        const jobs=results.filter(Boolean).map((x:any)=>x.job);
        if(jobs.length)setPdfJobs(prev=>[...prev,...jobs]);
      }
      setPicked([]);
      await loadBatch(id,true);
      if(errors.length)msg('Batch started with some upload errors',errors.slice(0,4).join('\n'));
      else msg('Batch started','All PDFs were uploaded. Conversion is running in the background.');
    }catch(e:any){msg('Could not start batch',e.message)}finally{setPdfBusy(false)}
  }

  async function shareBatch(){
    if(!batchId||!done)return;
    try{
      if(Platform.OS==='web'){msg('Use Admin Portal','Batch ZIP sharing is available in the Android app. The web portal can download converted files individually.');return}
      const base=FileSystem.cacheDirectory||FileSystem.documentDirectory;
      if(!base)throw new Error('No writable download folder is available.');
      const path=base+\`constituency-batch-\${batchId}.zip\`;
      const out=await FileSystem.downloadAsync(API+'/admin/pdf/batches/'+encodeURIComponent(batchId)+'/xlsx.zip',path,{headers:{Authorization:\`Bearer \${token}\`}});
      if(out.status!==200)throw new Error(\`Download failed (HTTP \${out.status})\`);
      if(await Sharing.isAvailableAsync())await Sharing.shareAsync(out.uri,{mimeType:'application/zip',dialogTitle:'Export converted Excel files'});
      else msg('Batch downloaded',out.uri);
    }catch(e:any){msg('Could not export batch',e.message)}
  }

  useEffect(()=>{if(!batchId||!active)return;const id=setInterval(()=>loadBatch(batchId,true),2500);return()=>clearInterval(id)},[batchId,active]);

  return <SafeAreaView style={styles.safe}><StatusBar style="dark"/><View style={styles.header}><View><Text style={styles.appTitle}>Admin</Text><Text style={styles.muted}>{user.fullName}</Text></View><Pill text={online?'ONLINE':'OFFLINE'} tone={online?'green':'white'}/></View><ScrollView contentContainerStyle={{padding:16,gap:12,paddingBottom:40}}>
    <View style={styles.card}><Text style={styles.title}>Fast multi-PDF converter</Text><Text style={styles.muted}>Select up to 50 constituency PDFs at once. Text PDFs skip OCR; scanned PDFs use adaptive fast OCR. The server queues several files safely instead of freezing the app.</Text><Button label="Select multiple PDFs" secondary onPress={pickPdfs}/>{picked.length>0&&<Text style={[styles.muted,{marginTop:8}]}>{picked.length} PDF{picked.length===1?'':'s'} selected</Text>}<Button label={pdfBusy?'Uploading…':\`Upload & convert \${picked.length||''}\`} disabled={pdfBusy||!picked.length||!online} onPress={startBatch}/>
      {!!batchId&&<View style={{marginTop:12,gap:8}}><View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center'}}><Text style={{fontWeight:'900',color:TEXT}}>Current batch</Text><Pill text={done?'DONE':failed?'CHECK ERRORS':active?'PROCESSING':'QUEUED'} tone={done?'green':failed?'red':active?'yellow':'white'}/></View><Text style={styles.muted}>{pdfJobs.length} file{pdfJobs.length===1?'':'s'} · {pdfJobs.filter(j=>j.status==='done').length} completed{failed?\` · \${failed} failed\`:''}</Text>{pdfJobs.map(j=><View key={j.jobId} style={{borderTopWidth:1,borderTopColor:BORDER,paddingTop:8}}><Text numberOfLines={1} style={{fontWeight:'800',color:TEXT}}>{j.filename}</Text><Text style={styles.muted}>{j.status==='done'?'100':j.progress||0}% · {j.message||j.status}</Text></View>)}<View style={{flexDirection:'row',gap:8,flexWrap:'wrap'}}><Button label="Refresh" small secondary onPress={()=>loadBatch()}/>{done&&<Button label="Share all Excel files (.zip)" small onPress={shareBatch}/>}</View></View>}
    </View>
    <View style={styles.card}><Text style={styles.title}>Admin web portal</Text><Text style={styles.muted}>Manage constituencies, booth uploads, volunteer approvals/reassignments, operational performance, referrals, 90-day consented GPS history and exports.</Text><Button label="Open Admin Portal" onPress={()=>Linking.openURL('https://elecdemo01.onrender.com/v4/admin-ops')}/></View>
    <View style={styles.card}><Text style={styles.title}>Access & privacy</Text><Text style={styles.muted}>Volunteers only receive their active booth. GPS tracking is off by default and starts only after the volunteer grants permission and taps Start GPS. Old GPS records are retained for 90 days.</Text></View><Button label="Logout" secondary onPress={onLogout}/>
  </ScrollView></SafeAreaView>
}`;

s = s.slice(0,start) + replacement + s.slice(end);

if (!s.includes('Select multiple PDFs')) throw new Error('Batch converter UI was not inserted');
if (!s.includes('/admin/pdf/bulk/single')) throw new Error('Native single-file batch endpoint missing');
if (!s.includes("expo-file-system/legacy")) throw new Error('Expo FileSystem legacy import missing');

fs.writeFileSync(target,s);
console.log(`Patched ${target}: fast multi-PDF Android batch converter.`);
