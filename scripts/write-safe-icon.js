const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const W = 512, H = 512;
const rgb = Buffer.alloc(W * H * 3);

function color(hex) {
  const s = hex.replace('#','');
  return [parseInt(s.slice(0,2),16), parseInt(s.slice(2,4),16), parseInt(s.slice(4,6),16)];
}
const PURPLE = color('#24135F');
const PURPLE_2 = color('#5B43B4');
const WHITE = color('#FFFFFF');
const SAFFRON = color('#FF9933');
const GREEN = color('#138808');

function setPixel(x,y,c){
  x=Math.round(x); y=Math.round(y);
  if(x<0||x>=W||y<0||y>=H)return;
  const i=(y*W+x)*3;
  rgb[i]=c[0]; rgb[i+1]=c[1]; rgb[i+2]=c[2];
}
function fill(c){
  for(let y=0;y<H;y++)for(let x=0;x<W;x++)setPixel(x,y,c);
}
function rect(x,y,w,h,c){
  for(let yy=Math.max(0,y);yy<Math.min(H,y+h);yy++)
    for(let xx=Math.max(0,x);xx<Math.min(W,x+w);xx++)setPixel(xx,yy,c);
}
function circle(cx,cy,r,c){
  const r2=r*r;
  for(let y=Math.floor(cy-r);y<=Math.ceil(cy+r);y++)
    for(let x=Math.floor(cx-r);x<=Math.ceil(cx+r);x++)
      if((x-cx)*(x-cx)+(y-cy)*(y-cy)<=r2)setPixel(x,y,c);
}
function roundRect(x,y,w,h,r,c){
  rect(x+r,y,w-2*r,h,c); rect(x,y+r,w,h-2*r,c);
  circle(x+r,y+r,r,c); circle(x+w-r-1,y+r,r,c);
  circle(x+r,y+h-r-1,r,c); circle(x+w-r-1,y+h-r-1,r,c);
}

fill(PURPLE);
// Subtle inner badge for depth.
roundRect(62,62,388,388,72,PURPLE_2);
roundRect(78,78,356,356,62,PURPLE);

// Clipboard / voter-list card.
roundRect(132,112,248,286,30,WHITE);
roundRect(196,88,120,54,20,WHITE);
roundRect(215,104,82,22,10,PURPLE);

// Person silhouette.
circle(213,188,36,PURPLE);
roundRect(160,226,106,62,30,PURPLE);

// Checklist rows.
for (const y of [310,346]) {
  roundRect(170,y,24,24,5,PURPLE);
  roundRect(210,y+5,126,14,7,PURPLE);
}

// Neutral India-inspired accents.
roundRect(118,404,116,12,6,SAFFRON);
roundRect(278,404,116,12,6,GREEN);
circle(256,410,7,WHITE);

function crc32(buf){
  let crc=0xffffffff;
  for(const b of buf){
    crc ^= b;
    for(let k=0;k<8;k++) crc=(crc>>>1)^((crc&1)?0xedb88320:0);
  }
  return (crc^0xffffffff)>>>0;
}
function chunk(type,data){
  const t=Buffer.from(type,'ascii');
  const out=Buffer.alloc(12+data.length);
  out.writeUInt32BE(data.length,0); t.copy(out,4); data.copy(out,8);
  out.writeUInt32BE(crc32(Buffer.concat([t,data])),8+data.length);
  return out;
}

// PNG scanline filter is explicitly 0 for every row. This is intentionally
// conservative because Expo SDK 54's jimp-compact decoder rejected the prior
// PNG encoder's filter stream on GitHub Actions.
const raw=Buffer.alloc(H*(1+W*3));
for(let y=0;y<H;y++){
  const dst=y*(1+W*3); raw[dst]=0;
  rgb.copy(raw,dst+1,y*W*3,(y+1)*W*3);
}
const ihdr=Buffer.alloc(13);
ihdr.writeUInt32BE(W,0); ihdr.writeUInt32BE(H,4);
ihdr[8]=8; ihdr[9]=2; ihdr[10]=0; ihdr[11]=0; ihdr[12]=0;
const png=Buffer.concat([
  Buffer.from([137,80,78,71,13,10,26,10]),
  chunk('IHDR',ihdr),
  chunk('IDAT',zlib.deflateSync(raw,{level:6})),
  chunk('IEND',Buffer.alloc(0)),
]);

const target=process.argv[2]||'assets/icon.png';
fs.mkdirSync(path.dirname(target),{recursive:true});
fs.writeFileSync(target,png);
console.log(`Wrote Jimp-safe 512x512 RGB icon to ${target} (${png.length} bytes)`);
