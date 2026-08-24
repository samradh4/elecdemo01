from pathlib import Path
import json
import re

API_URL = "https://elecdemo01.onrender.com"
APP_VERSION = "3.7.0"
VERSION_CODE = 370

p = Path("App.tsx")
s = p.read_text()

s = s.replace(
    "function defaultApiUrl() { return isWeb() ? 'http://127.0.0.1:8000' : ''; }",
    f"function defaultApiUrl() {{ return '{API_URL}'; }}",
)

s = s.replace(
    "if(raw){ try{ const s=JSON.parse(raw); if(s.apiUrl!==undefined)setApiUrl(s.apiUrl); if(s.apiKey)setApiKey(s.apiKey); if(s.org)setOrg(s.org); }catch{} }",
    "if(raw){ try{ const s=JSON.parse(raw); setApiUrl(defaultApiUrl()); if(s.apiKey)setApiKey(s.apiKey); if(s.org)setOrg(s.org); }catch{} } else { setApiUrl(defaultApiUrl()); }",
)

s = s.replace("const timeout=setTimeout(()=>controller.abort(), options.timeoutMs||15000);", "const timeout=setTimeout(()=>controller.abort(), options.timeoutMs||30000);")
s = s.replace("await request('/health',{timeoutMs:5000});", "await request('/health',{timeoutMs:90000});")
s = s.replace("await request('/health',{timeoutMs:2500});", "await request('/health',{timeoutMs:65000});")
s = s.replace("for(let i=0;i<12;i++){", "for(let i=0;i<2;i++){")
s = s.replace("await new Promise(r=>setTimeout(r,750));", "await new Promise(r=>setTimeout(r,1500));")
s = s.replace("if(e?.name==='AbortError') throw new Error('Backend timed out. Make sure the API server is running on port 8000.');", "if(e?.name==='AbortError') throw new Error('Cloud server took too long to respond. Check internet and retry.');")
s = s.replace("if(String(e?.message||e).includes('Failed to fetch')) throw new Error(`Backend is not reachable at ${apiUrl}. Start the backend, then retry.`);", "if(String(e?.message||e).includes('Failed to fetch')) throw new Error('Cloud server is temporarily unreachable. Check internet and retry.');")

pattern = re.compile(r"  async function uploadFile\(path:string,accept:string,mime:string\)\{.*?\n  \}\n  async function importCsv", re.S)
replacement = r'''  async function uploadFile(path:string,accept:string,mime:string){
    if(!apiUrl) throw new Error('Cloud server is not configured');
    const endpoint=`${apiUrl.replace(/\/$/,'')}${path}`;
    if(isWeb()){
      const file=await pickWebFile(accept); if(!file)return null;
      const form=new FormData(); form.append('file',file,file.name);
      const res=await fetch(endpoint,{method:'POST',headers:{...(apiKey?{'X-App-Key':apiKey}:{})},body:form});
      const ct=res.headers.get('content-type')||'';
      const j=ct.includes('json')?await res.json():{};
      if(!res.ok)throw new Error(formatApiErrorDetail(j?.detail)||`HTTP ${res.status}`);
      return j;
    }

    // Android file providers often report CSV files as generic documents. Accept
    // any document, then validate its extension so downloaded CSVs remain tappable.
    const r=await DocumentPicker.getDocumentAsync({type:'*/*',copyToCacheDirectory:true});
    if(r.canceled)return null;
    const a=r.assets[0];
    const lower=String(a.name||'').toLowerCase();
    if(path==='/import/csv' && !lower.endsWith('.csv')) throw new Error('Please choose a .csv file.');
    if(path==='/pdf/jobs' && !lower.endsWith('.pdf')) throw new Error('Please choose a .pdf file.');

    // Wake the service before uploading. This removes the common cold-start
    // upload failure and is harmless on an always-on production instance.
    await request('/health',{timeoutMs:90000});

    try{
      const result=await FileSystem.uploadAsync(endpoint,a.uri,{
        httpMethod:'POST',
        uploadType:FileSystem.FileSystemUploadType.MULTIPART,
        fieldName:'file',
        mimeType:a.mimeType||mime,
        headers:{...(apiKey?{'X-App-Key':apiKey}:{})},
      });
      let j:any={};
      try{j=JSON.parse(result.body||'{}')}catch{}
      if(result.status<200||result.status>=300)throw new Error(formatApiErrorDetail(j?.detail)||`HTTP ${result.status}`);
      return j;
    }catch(e:any){
      const msg=String(e?.message||e);
      if(/network request failed|network error|failed to fetch|socket|timeout/i.test(msg)) throw new Error('Upload could not reach the cloud server. Check internet, wait a few seconds, and retry.');
      throw e;
    }
  }
  async function importCsv'''
s, n = pattern.subn(replacement, s, count=1)
if n != 1:
    raise SystemExit("Could not patch uploadFile function")

s = s.replace('<Field label="Backend URL" value={apiUrl} onChangeText={setApiUrl} placeholder="http://127.0.0.1:8000"/>', '<Text style={styles.helpText}>Cloud server is configured automatically.</Text>')
s = s.replace('<Field label="API key (optional)" value={apiKey} onChangeText={setApiKey} placeholder="Only if APP_API_KEY is enabled"/>', '')
s = s.replace('<Text style={styles.helpText}>Mac/Windows browser: http://127.0.0.1:8000. Physical phone: use your computer\'s LAN IP, e.g. http://192.168.1.5:8000.</Text>', '<Text style={styles.helpText}>No backend setup is required on this device.</Text>')
s = s.replace('label="Save settings"', 'label="Save name"')

required = [
    API_URL,
    "DocumentPicker.getDocumentAsync({type:'*/*'",
    "FileSystem.uploadAsync(endpoint",
    "await request('/health',{timeoutMs:90000});",
    "Cloud server is configured automatically.",
]
for item in required:
    if item not in s:
        raise SystemExit(f"Missing expected production patch: {item}")

p.write_text(s)

ap = Path("app.json")
d = json.loads(ap.read_text())
d["expo"]["version"] = APP_VERSION
d["expo"].setdefault("android", {})["package"] = "com.constituencymanager.app"
d["expo"]["android"]["versionCode"] = VERSION_CODE
ap.write_text(json.dumps(d, indent=2) + "\n")

print(f"Patched client {APP_VERSION} for {API_URL}")
