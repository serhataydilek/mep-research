"""Convert a completed medical-dental fixture into deterministic lazy-load chunks."""
from __future__ import annotations
import argparse,json,hashlib,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'))+'\n'
def build(source:Path,manifest:Path,chunk_dir:Path,size:int=100):
 started=time.perf_counter(); data=json.loads(source.read_text(encoding='utf8')); chunk_dir.mkdir(parents=True,exist_ok=True); layers=[]
 for layer in data['layers']:
  refs=[]; meshes=layer.pop('meshes')
  for number,start in enumerate(range(0,len(meshes),size)):
   part={'schema_version':1,'kind':'REAL_IFC_GEOMETRY_CHUNK','layer_id':layer['id'],'chunk_index':number,'meshes':meshes[start:start+size]}; name=f"{layer['id']}_{number:03d}.json"; body=canonical(part);(chunk_dir/name).write_text(body,encoding='utf8');refs.append({'url':f'{chunk_dir.name}/{name}','mesh_count':len(part['meshes']),'sha256':hashlib.sha256(body.encode()).hexdigest()})
  layer['chunk_count']=len(refs);layer['chunks']=refs;layers.append(layer)
 result={k:v for k,v in data.items() if k!='layers'}|{'schema_version':5,'kind':'REAL_IFC_PROGRESSIVE_MANIFEST','layers':layers,'progressive_loading':{'chunk_mesh_cap':size,'source_fixture_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'conversion_seconds':round(time.perf_counter()-started,6)}}
 manifest.write_text(canonical(result),encoding='utf8');print(json.dumps({'manifest':str(manifest),'chunks':sum(x['chunk_count'] for x in layers),'seconds':result['progressive_loading']['conversion_seconds']}))
def main():
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=ROOT/'viewer/medical_dental_clinic_fixture.json');p.add_argument('--manifest',type=Path,default=ROOT/'viewer/medical_dental_clinic_progressive.json');p.add_argument('--chunk-dir',type=Path,default=ROOT/'viewer/medical_dental_chunks');p.add_argument('--chunk-size',type=int,default=100);a=p.parse_args();build(a.source,a.manifest,a.chunk_dir,a.chunk_size)
if __name__=='__main__':main()
