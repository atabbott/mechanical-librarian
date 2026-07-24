#!/usr/bin/env python3
"""Bounded aggregate-only recoverability evaluator; never publishes rows."""
from __future__ import annotations
import argparse, json
from collections import Counter
from pathlib import Path

INPUT_SCHEMA="recoverability-evaluator-v2"; OUTPUT_SCHEMA="recoverability-aggregate-v2"
SUPPORTED=("title","subtitle","creator","contributor","publisher","language","publication_date","identifier","collection","collection_position")
EXPLORATORY=("edition","rights")
CATEGORIES=("absent_in_source","inspection_failure","extracted_not_projected","normalization_mismatch","semantic_mismatch","matched_partial","matched_complete")
ISBN=("accepted","associated","unrelated","invalid","unresolved")
STATES=("value","unknown","known_empty","not_applicable","accepted_unsupported","unexplained_failure")
ROW_KEYS={"field","state","readable","inspection","source","extraction","projection","comparison"}
IDENTITY_KEYS={"relation","provider_scope","same_work","non_corroborating","source_present"}
MAX_BYTES=1_000_000; MAX_ROWS=2_000; MAX_DEPTH=20; MAX_INTEGER_DIGITS=12

def _pairs(pairs):
 d={}
 for k,v in pairs:
  if k in d: raise ValueError("duplicate JSON key")
  d[k]=v
 return d
def _integer(v):
 if len(v.lstrip("-"))>MAX_INTEGER_DIGITS: raise ValueError("integer too large")
 return int(v)
def load(path):
 b=path.read_bytes()
 if len(b)>MAX_BYTES: raise ValueError("input too large")
 try: x=json.loads(b.decode("utf-8"),object_pairs_hook=_pairs,parse_int=_integer,parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
 except (UnicodeDecodeError,json.JSONDecodeError,ValueError) as e: raise ValueError("invalid input") from e
 def depth(v,n=0):
  if n>MAX_DEPTH: raise ValueError("input too deep")
  if isinstance(v,dict): [depth(a,n+1) for a in v.values()]
  if isinstance(v,list): [depth(a,n+1) for a in v]
 depth(x); return x
def _category(r):
 if r["inspection"]=="failure" or r["extraction"]=="failure": return "inspection_failure"
 if r["source"]=="absent": return "absent_in_source"
 if r["source"]!="present" or r["extraction"]!="found": raise ValueError("invalid evidence stages")
 if r["projection"]=="not_projected": return "extracted_not_projected"
 if r["projection"]!="projected": raise ValueError("invalid projection")
 m=r["comparison"]
 return {"normalization":"normalization_mismatch","semantic":"semantic_mismatch","partial":"matched_partial","complete":"matched_complete"}.get(m) or (_ for _ in ()).throw(ValueError("invalid comparison"))
def _matrix(fields): return {f:{c:0 for c in CATEGORIES} for f in fields}
def evaluate(d):
 if not isinstance(d,dict) or set(d)!={"schema","rows","identifier_observations","regression"} or d["schema"]!=INPUT_SCHEMA or not isinstance(d["rows"],list) or not isinstance(d["identifier_observations"],list): raise ValueError("invalid document")
 if len(d["rows"])>MAX_ROWS or len(d["identifier_observations"])>MAX_ROWS: raise ValueError("too many rows")
 matrices={"supported":_matrix(SUPPORTED),"exploratory":_matrix(EXPLORATORY)}; excluded=Counter()
 for r in d["rows"]:
  if not isinstance(r,dict) or r.get("field") not in SUPPORTED+EXPLORATORY or r.get("state") not in STATES: raise ValueError("invalid row")
  field,state=r["field"],r["state"]
  if state!="value":
   if set(r)!={"field","state"}: raise ValueError("excluded row carries evidence")
   excluded[state]+=1; continue
  if set(r)!=ROW_KEYS or type(r["readable"]) is not bool or not r["readable"]: raise ValueError("readable value required")
  cat=_category(r); matrices["supported" if field in SUPPORTED else "exploratory"][field][cat]+=1
 isbn=Counter()
 for o in d["identifier_observations"]:
  if not isinstance(o,dict) or set(o)!=IDENTITY_KEYS or o.get("relation") not in ISBN: raise ValueError("invalid identifier observation")
  if o["relation"]=="associated" and not (o["provider_scope"] in {"hardcover"} and o["same_work"] is True and o["non_corroborating"] is True and o["source_present"] is True): raise ValueError("invalid associated ISBN")
  if o["relation"]=="associated" and o["source_present"] is not True: raise ValueError("source-missing ISBN cannot associate")
  isbn[o["relation"]]+=1
 reg=d["regression"]
 if not isinstance(reg,dict) or set(reg)!={"matches","denominator","candidate_precision","false_positive_rate"} or reg["matches"]!=257 or reg["denominator"]!=405 or reg["candidate_precision"]!=0.801775 or reg["false_positive_rate"]!=0.085366: raise ValueError("supported regression drift")
 def view(name):
  m=matrices[name]; return {"name":name,"evaluated_fields":list(SUPPORTED if name=="supported" else EXPLORATORY),"denominator":sum(sum(v.values()) for v in m.values()),"by_field":m,"categories":{c:sum(m[f][c] for f in m) for c in CATEGORIES}}
 return {"schema":OUTPUT_SCHEMA,"views":{"supported":view("supported"),"exploratory":view("exploratory")},"excluded":{s:excluded[s] for s in STATES if s!="value"},"isbn":{"counts":{x:isbn[x] for x in ISBN},"denominator":sum(isbn.values())},"regression":{"matches":257,"denominator":405,"candidate_precision":0.801775,"false_positive_rate":0.085366},"recommendations":[]}
def main():
 p=argparse.ArgumentParser();p.add_argument("--input",required=True,type=Path);p.add_argument("--output",required=True,type=Path);a=p.parse_args()
 try: result=evaluate(load(a.input)); a.output.write_text(json.dumps(result,sort_keys=True)+"\n",encoding="utf-8")
 except (OSError,ValueError): raise SystemExit("Recoverability evaluation failed safely.")
if __name__=="__main__": main()
