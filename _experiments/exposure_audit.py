"""Audit: do the drawn schematics realize the predicted zones under the dataset's exposure rule?"""
import sys, os, time, math
os.environ["QT_QPA_PLATFORM"]="offscreen"
sys.path.insert(0, "/home/claude/work/app"); os.chdir("/home/claude/work/app")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
app=QApplication([]); QSettings("ARML","LayoutGenerator").clear()
from app.main_window import MainWindow
from models import zone_scoring as zs
from app.widgets.schematic_canvas import get_synthesis
from models.schematic_layout import OUTDOOR_TYPES

def realized_zone(syn, rid, metre):
    """Exposure rule of §3.2.3 applied to the drawn rectangles."""
    r = next(x for x in syn.rects if x["room_id"]==rid)
    ext = {"N":0.0,"S":0.0,"E":0.0,"W":0.0}
    for w in syn.walls:
        if w["rooms"][0]!=rid or w["kind"]!="ext": continue
        # free exterior, or facing a balcony (counts as exposed)
        ext[w["side"]] += math.hypot(w["x2"]-w["x1"], w["y2"]-w["y1"])
    extent = {"N":r["w"],"S":r["w"],"E":r["h"],"W":r["h"]}
    exposed = {s for s in ext if ext[s] >= max(1.0*metre, 0.25*extent[s])}
    ROW={"N":0,"S":2}; COL={"W":0,"E":2}
    def zone(sides):
        rows=[ROW[s] for s in sides if s in ROW]; cols=[COL[s] for s in sides if s in COL]
        return (rows[0] if rows else 1)*3 + (cols[0] if cols else 1)
    if not exposed: return 4, exposed
    if len(exposed)==1: return zone(exposed), exposed
    if len(exposed)==2:
        if exposed=={"N","S"} or exposed=={"E","W"}:
            s=max(exposed,key=lambda k: ext[k]); return zone({s}), exposed
        return zone(exposed), exposed
    # 3 or 4: corner of the two longest adjacent exposures
    best=None
    for a in exposed:
        for b in exposed:
            if a<b and {a,b} not in ({"N","S"},{"E","W"}):
                v=ext[a]+ext[b]
                if best is None or v>best[0]: best=(v,{a,b})
    return zone(best[1]), exposed

def run(room_counts, label):
    win=MainWindow(); win.resize(2560,1400); win.show(); app.processEvents()
    inp=win.stage_panels["inputs"]; area=win.stage_panels["area"]; zones=win.stage_panels["zones"]; conn=win.stage_panels["connectivity"]; sch=win.stage_panels["schematic"]
    def pump(n=30):
        for _ in range(n): app.processEvents(); time.sleep(0.005)
    for k,v in room_counts.items(): inp._counter_rows[k].set_value(v)
    pump(); area.regenerate(); pump(); area._select(1); pump(); zones.regenerate(); pump(); conn.regenerate(); pump(); sch.regenerate(); pump(50)
    st2=win.store.stage2_zones; st4=win.store.stage4_sch
    tbl={int(t["table_index"]):t for t in st2.tables}
    site=zs.site_from_inputs(win.store.inputs.site_conditions)
    print(f"\n=== {label}: {len(st4.schematics)} plans ===")
    tot=0; same=0; dq=[]; da=[]
    for s in st4.schematics:
        syn=get_synthesis(s,"N"); t=tbl[int(s["plan_index"])]
        rooms=[r for r in t["rooms"] if r.get("zone_assignment") is not None]
        zrooms=[{"id":r["room_id"],"type":r["room_type"],"area":r["area_m2"],"w":math.sqrt(r["area_m2"]/1.2),"h":math.sqrt(r["area_m2"]*1.2),"perim":0,"wwr":r.get("wwr",0.3),"edr":0.0,"has_front_door":False} for r in rooms]
        for z in zrooms: z["perim"]=2*(z["w"]+z["h"])
        A,Q,L,zv=zs.tables(zrooms,site); w=zs.weights(zrooms)
        pred=[int(r["zone_assignment"])+1 for r in rooms]
        real=[]; notes=[]
        for r in rooms:
            if r["room_id"] in syn.removed: real.append(int(r["zone_assignment"])+1); continue
            rz,exp=realized_zone(syn,r["room_id"],syn.metre); real.append(rz+1)
            tot+=1; same+= (rz==int(r["zone_assignment"]))
            if rz!=int(r["zone_assignment"]) and r["room_type"] in zs.SCORED_TYPES:
                notes.append(f"{r['room_id']} Z{r['zone_assignment']+1}->Z{rz+1}{''.join(sorted(exp))}")
        q0,a0=zs.evaluate(pred,A,Q,w); q1,a1=zs.evaluate(real,A,Q,w)
        dq.append(q1-q0); da.append(a1-a0)
        print(f"#{s['plan_index']:02d} Q {q0:.3f}->{q1:.3f} ({q1-q0:+.3f})  A {a0:.3f}->{a1:.3f} ({a1-a0:+.3f})  " + "; ".join(notes))
    print(f"rooms keeping their zone: {same}/{tot} = {100*same/tot:.0f}%   mean |dQ| {sum(map(abs,dq))/len(dq):.3f}  mean |dA| {sum(map(abs,da))/len(da):.3f}  max |dQ| {max(map(abs,dq)):.3f} max |dA| {max(map(abs,da)):.3f}")
    win.close()

run({"living":1,"bedroom":2,"kitchen":1,"bathroom":1,"balcony":1}, "6 rooms")
run({"living":1,"bedroom":3,"kitchen":1,"bathroom":3,"balcony":3}, "11 rooms")
