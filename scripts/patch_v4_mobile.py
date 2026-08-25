from pathlib import Path
p=Path('App.tsx')
s=p.read_text()
s=s.replace("let db: SQLite.SQLiteDatabase | null=null;","let db: SQLite.SQLiteDatabase | null=null;\nlet lastNetConnected:boolean|null=null;")
s=s.replace("      if(!asg.active){if(cached?.assignment){await clearLocalBooth()}const s={user:session!.user,assignment:null,offlineLeaseUntil:null};setSession(s);await metaSet('session',s);await loadLocal();if(!silent)msg('Access updated',asg.pending?'Your booth request is waiting for admin approval.':'No active booth is assigned.');return}","      const activeUser=session?.user||cached?.user;\n      if(!activeUser)throw new Error('Please login again.');\n      if(!asg.active){if(cached?.assignment){await clearLocalBooth()}const s={user:activeUser,assignment:null,offlineLeaseUntil:null};setSession(s);await metaSet('session',s);await loadLocal();if(!silent)msg('Access updated',asg.pending?'Your booth request is waiting for admin approval.':'No active booth is assigned.');return}")
s=s.replace("      const s:Session={user:session!.user,assignment:asg.active,offlineLeaseUntil:lease};","      const s:Session={user:activeUser,assignment:asg.active,offlineLeaseUntil:lease};")
old="  useEffect(()=>{const sub=NetInfo.addEventListener(state=>{const is=!!state.isConnected;setOnline(is);if(is&&token&&session?.user?.role==='volunteer')syncNow(true)});return()=>sub()},[token,session?.user?.role,syncNow]);"
new="  useEffect(()=>{const sub=NetInfo.addEventListener(state=>{const is=!!state.isConnected;const shouldSync=is&&(lastNetConnected===null||lastNetConnected===false);lastNetConnected=is;setOnline(is);if(shouldSync&&token&&session?.user?.role==='volunteer')syncNow(true)});return()=>sub()},[token,session?.user?.role,syncNow]);"
s=s.replace(old,new)
for item in ["const activeUser=session?.user||cached?.user;","lastNetConnected:boolean|null=null","const shouldSync=is&&(lastNetConnected===null||lastNetConnected===false);"]:
    if item not in s: raise SystemExit('patch failed: '+item)
p.write_text(s)
