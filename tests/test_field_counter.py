import base64
import csv
import io
import json
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from dean import coworking
from dean.field_tracking import Counter
from tests.test_dean import DemoFixture,Client


def box(x,y=.45):
    return (x-.055,y-.15,x+.055,y+.15)


class TrackTest(TestCase):
    def test_two_directions_together_and_reverse(self):
        counter=Counter("x",50,1)
        changes=[]
        xs=[.20,.26,.34,.42,.49,.57,.65,.71]
        for i,x in enumerate(xs):
            people=[box(x,.38),box(1-x,.66)]
            changes+=counter.update(people if i%2 else people[::-1], i*.14)
        self.assertCountEqual(changes,[1,-1])

    def test_direction_pause_and_jitter(self):
        counter=Counter("y",50,-1)
        def sample(t,y):
            return counter.update([box(.4,y-.015)],t)
        events=[]
        for i,y in enumerate((.31,.34,.45,.48,.52,.49,.47,.52,.57,.62,.54,.47,.40,.35)):
            events+=sample(i*.14,y)
        self.assertEqual(events,[-1,1])
        self.assertFalse(sample(10,.77))
        self.assertFalse(sample(10.1,.8))

    def test_people_walking_past_the_opening_do_not_count(self):
        for y,expected in ((.2,[]),(.5,[1]),(.85,[])):
            counter=Counter("x",50,1,30,70)
            events=[]
            for i,x in enumerate((.3,.31,.38,.46,.53,.58,.66,.7)):
                events+=counter.update([box(x,y)],i*.12)
            self.assertEqual(events,expected)

    def test_a_single_appearance_is_not_a_crossing(self):
        tr=Counter("x",50,1)
        self.assertEqual(tr.update([box(.3)],0),[])
        self.assertEqual(tr.update([],1),[])
        self.assertEqual(tr.update([box(.7)],2),[])
        self.assertEqual(tr.update([box(.7)],2.15),[])


class FieldFlow(DemoFixture):
    def setUp(self):
        super().setUp()
        flag=patch.dict("os.environ",{"DEAN_CAMPUS_LAB":"1"})
        flag.start(); self.addCleanup(flag.stop)
        self.staff,self.viewer=Client(self.app),Client(self.app)
        for c,name in [(self.staff,"staff"),(self.viewer,"student-a")]:
            c.login(name,self.passwords[name])
        self.data=self.staff.fields("/coworking","/coworking") |dict(room="Дверь A",kind="practice",capacity="35",initial="4",axis="x",line="50",inside="1")
        reply=self.staff.request("/coworking",self.data)
        self.assertEqual(reply[0],303)
        self.path=reply[1]["Location"]
        self.csrf=self.data["csrf"]
        self.key=uuid4().hex

    def post(self,action,**data):
        return self.staff.request(self.path+"/"+action,{"camera":self.key,"csrf":self.csrf,**data})

    def state(self):
        return json.loads(self.viewer.request(self.path+"/status")[2])

    def test_independent_sessions_permissions_journal_and_failures(self):
        self.assertEqual(self.staff.request("/coworking",self.data)[1]["Location"],self.path)
        self.assertEqual(self.state()["initial"],4)
        viewer_csrf=self.viewer.fields(self.path,"/logout")["csrf"]
        self.assertEqual(self.viewer.request(self.path+"/claim",dict(csrf=viewer_csrf,camera=self.key))[0],403)
        self.assertEqual(self.post("claim")[0],200)
        self.assertEqual(self.post("claim",camera=uuid4().hex)[0],409)
        self.assertEqual(self.post("frame",jpeg="XX",seq="1")[0],422)
        self.assertEqual(self.post("frame",jpeg="XX",seq="2")[0],409)
        raw=b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x40\x00\x40"+bytes(100)
        jpeg=base64.b64encode(raw).decode()
        xs=[.27,.29,.35,.43,.49,.56,.61,.64,.61,.54,.47,.40,.34]
        with patch("dean.field_detector.detect",side_effect=([box(x)] for x in xs)), patch("dean.coworking.time.monotonic",side_effect=[i*.15 for i in range(len(xs))]):
            for seq in range(1,len(xs)+1):
                self.assertEqual(self.post("frame",jpeg=jpeg,seq=str(seq))[0],200)
                if seq==7:
                    self.assertEqual(self.post("frame",jpeg=jpeg,seq="7")[0],200)
        state=self.state()
        self.assertEqual((state["enter"],state["exit"],state["occupancy"]),(1,1,4))
        self.assertEqual(self.post("pause",request_id=uuid4().hex)[0],200)
        self.assertTrue(self.state()["check"])
        self.assertEqual(self.post("recount",request_id=uuid4().hex,count="6")[0],303)
        self.assertFalse(self.state()["check"])
        self.assertEqual(self.post("end",request_id=uuid4().hex)[0],303)
        self.assertEqual(self.post("claim")[0],409)
        report=list(csv.DictReader(io.StringIO(self.viewer.request(self.path+"/export.csv")[2].lstrip("\ufeff"))))
        self.assertEqual([r["event"] for r in report],["start","resume","enter","exit","pause","recount","end"])
        self.assertEqual(report[-1]["inside_after"],"6")
        self.assertNotIn(self.key,str(report))
        reference=self.staff.fields(self.path,self.path+"/reference")
        self.assertEqual(self.staff.request(self.path+"/reference",reference|dict(entered="1",exited="2"))[0],303)
        self.assertIn("Сверка с наблюдателем",self.viewer.request(self.path)[2])
        self.assertEqual(self.state()["heat"][0]["frames"],len(xs))
