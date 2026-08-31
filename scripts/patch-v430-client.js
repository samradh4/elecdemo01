const fs = require('fs');

const target = process.argv[2] || 'App.tsx';
let s = fs.readFileSync(target, 'utf8');

const searchStart = s.indexOf("async function localSearch(q=''){");
const searchEnd = s.indexOf('\nasync function queueMutation', searchStart);
if (searchStart < 0 || searchEnd < 0) throw new Error('localSearch markers not found');

const replacement = String.raw`function normalizeLookup(value:any){
  return String(value||'').normalize('NFKD').toLowerCase().replace(/[\u0300-\u036f]/g,'').replace(/\s+/g,' ').trim();
}
function romanizeHindi(value:any){
  const src=String(value||'');
  const independent:any={'अ':'a','आ':'aa','इ':'i','ई':'ee','उ':'u','ऊ':'oo','ऋ':'ri','ए':'e','ऐ':'ai','ओ':'o','औ':'au'};
  const consonant:any={'क':'k','ख':'kh','ग':'g','घ':'gh','ङ':'n','च':'ch','छ':'chh','ज':'j','झ':'jh','ञ':'n','ट':'t','ठ':'th','ड':'d','ढ':'dh','ण':'n','त':'t','थ':'th','द':'d','ध':'dh','न':'n','प':'p','फ':'ph','ब':'b','भ':'bh','म':'m','य':'y','र':'r','ल':'l','व':'v','श':'sh','ष':'sh','स':'s','ह':'h','क़':'q','ख़':'kh','ग़':'g','ज़':'z','ड़':'d','ढ़':'dh','फ़':'f'};
  const matra:any={'ा':'aa','ि':'i','ी':'ee','ु':'u','ू':'oo','ृ':'ri','े':'e','ै':'ai','ो':'o','ौ':'au'};
  let out='';
  for(let i=0;i<src.length;i++){
    const ch=src[i], next=src[i+1]||'';
    if(independent[ch]){out+=independent[ch];continue}
    if(consonant[ch]){
      out+=consonant[ch];
      if(next==='्')continue;
      if(matra[next])continue;
      out+='a';continue;
    }
    if(matra[ch]){out+=matra[ch];continue}
    if(ch==='ं'||ch==='ँ'){out+='n';continue}
    if(ch==='ः'){out+='h';continue}
    if(ch==='्')continue;
    if(/[A-Za-z0-9\s./-]/.test(ch))out+=ch;
  }
  return normalizeLookup(out).replace(/aa/g,'a').replace(/ee/g,'i').replace(/oo/g,'u').replace(/a\b/g,'').replace(/\s+/g,' ').trim();
}
function voterMatchesLookup(v:Voter,q:string){
  const n=normalizeLookup(q);if(!n)return true;
  const fields=[v.name,v.localName,v.epicId,v.relativeName,v.houseNo,romanizeHindi(v.localName),romanizeHindi(v.relativeName)];
  return fields.some(x=>normalizeLookup(x).includes(n));
}
async function localSearch(q=''){
  const d=await openDb();
  const rows=await d.getAllAsync<Voter>('SELECT * FROM voters ORDER BY CAST(serialNo AS INTEGER), name, localName LIMIT 2500');
  if(!q.trim())return rows.slice(0,2000);
  return rows.filter(v=>voterMatchesLookup(v,q)).slice(0,500);
}`;

s = s.slice(0, searchStart) + replacement + s.slice(searchEnd);

s = s.replace(
  'placeholder="Search English/Hindi name, EPIC, house"',
  'placeholder="Search English, हिंदी, Roman name, EPIC, house"'
);

const authNeedle = "if(out.bootstrapAdmin)msg('Admin account created','This first account is the administrator. Open the Admin Portal to configure the constituency, booths and volunteers.');await refreshSession(out.token)";
if (!s.includes(authNeedle)) throw new Error('registration message marker not found');
s = s.replace(
  authNeedle,
  "if(out.bootstrapAdmin)msg('Admin account created','This first account is the administrator. Open the Admin Portal to configure the constituency, booths and volunteers.');else if(kind==='register')msg('Registration received','Your account now appears automatically in the admin approval queue. Select your constituency and booth to request access.');await refreshSession(out.token)"
);

if (!s.includes('romanizeHindi')) throw new Error('Romanized Hindi search was not inserted');
if (!s.includes('admin approval queue')) throw new Error('Registration approval message was not inserted');

fs.writeFileSync(target, s);
console.log(`Patched ${target}: English/Hindi/Romanized voter search and registration approval notice.`);
