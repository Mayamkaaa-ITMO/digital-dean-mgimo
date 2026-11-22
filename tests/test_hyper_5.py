from contextlib import closing
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dean import clock, services, telegram, telegram_worker, workspace
from dean.db import connect, write
from dean.domain import DomainError, timestamp
from dean.telegram_transport import BotAPI, TelegramConfig, TransportError
from tests.test_dean import Client, DemoFixture
from tests.test_night_l import FIRST, choose

T=datetime.fromisoformat("2026-11-18T20:00:00+00:00")
ROOT="/profile/telegram"
CHATS=(620001,620002)
CONFIG=TelegramConfig("polling","dean_fixture_bot","",frozenset(CHATS),"")
ENV={"DEAN_TELEGRAM_MODE":"polling","DEAN_TELEGRAM_BOT_USERNAME":CONFIG.name,
     "DEAN_TELEGRAM_ALLOWED_CHAT_IDS":",".join(map(str,CHATS)),"DEAN_TELEGRAM_APP_URL":""}


class FakeTransport:
    def __init__(self):
        self.batch=[]
        self.sent=[]
        self.attempts=[]
        self.offsets=[]
        self.failures=[]
        self.menus=[]

    def updates(self,offset):
        self.offsets.append(offset)
        answer,self.batch=self.batch,[]
        return answer

    def send(self,dest,text,*,reply_markup=None):
        self.attempts.append((dest,text))
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append((dest,text))
        self.menus.append(reply_markup)
        return len(self.sent)


def message(uid,chat,text,kind="private"):
    return {"update_id":uid,"message":{"chat":{"type":kind,"id":chat},"from":{"id":chat},"text":text}}


class TelegramTest(DemoFixture):
    def pump(self,tg,*updates,config=CONFIG):
        tg.batch=list(updates)
        with closing(connect(self.path)) as db:
            return telegram_worker.tick(db,config,tg)

    def test_branded_menu_reads_student_schedule_after_pairing(self):
        student = self.client("student-a")
        tg = FakeTransport()
        with patch.dict(os.environ, ENV), clock.at(T):
            self.connect_account(student, tg)
            self.pump(tg, message(2, CHATS[1], "🔗 Подключить кабинет"))
            self.assertIn("Привет, я Вишка", tg.sent[-1][1])
            self.pump(tg, message(3, CHATS[0], "📅 Сегодня"))
            self.assertIn("Расписание на сегодня", tg.sent[-1][1])
            self.assertIn("🏠 Кабинет", [item["text"] for row in tg.menus[-1]["keyboard"] for item in row])
        api = BotAPI(CONFIG)
        with patch.object(api, "call", return_value=True) as call:
            api.configure()
        self.assertEqual([item.args[0] for item in call.call_args_list],
                         ["setMyName", "setMyDescription", "setMyShortDescription", "setMyCommands",
                          "setChatMenuButton", "setMyProfilePhoto"])
        self.assertTrue(call.call_args.kwargs["avatar"].startswith(b"\xff\xd8"))

    def start(self,a):
        route=ROOT+"/begin"
        self.assertEqual(a.request(ROOT)[0],200)
        result=a.request(route,a.fields(ROOT,route))
        self.assertEqual(result[0],200)
        return re.search(r'start=([A-Za-z0-9_-]{43})',result[2])[1]

    def proof(self,tg,chat):
        return next(re.search(r'Код привязки: ([0-9a-f]{12})',text)[1]
                    for dest,text in reversed(tg.sent) if dest==chat and text.startswith("Код привязки:"))

    def connect_account(self,a,tg,chat=CHATS[0],config=CONFIG):
        nonce=self.start(a)
        self.pump(tg,message(1,chat,"/start "+nonce),config=config)
        route=ROOT+"/confirm"
        self.assertEqual(a.request(route,{**a.fields(ROOT,route),"proof":self.proof(tg,chat)})[0],303)
        self.pump(tg,config=config)
        self.assertIn("Кабинет подключён",tg.sent[-1][1])

    def task_notice(self,due):
        with closing(connect(self.path)) as db,write(db):
            user=db.execute("SELECT * FROM account WHERE username='student-a'").fetchone()
            tid=workspace.add_task(db,user,("Текст задачи, который нельзя отдавать в уведомлении","",None,timestamp(due),"normal"))
            services.notify(db,*workspace.scope(user),"task",tid,f"task:{tid}:{timestamp(due)}",
                            "Внутренний текст","Приватный текст",timestamp())
        return tid

    def test_two_sided_binding_one_owner_ignored_group_and_persistent_cursor(self):
        a,b=self.client("student-a"),self.client("student-b")
        tg=FakeTransport()
        with patch.dict(os.environ,ENV),clock.at(T):
            nonce=self.start(a)
            route=ROOT+"/confirm"
            self.assertIn("proof",a.fields(ROOT,route))
            early=a.request(route,{**a.fields(ROOT,route),"proof":"0"*12})
            self.assertEqual(early[0],409)
            self.assertIn('action="'+route+'"',early[2])
            self.pump(tg,message(1,CHATS[1],"/start "+nonce))
            foreign=self.proof(tg,CHATS[1])
            self.assertEqual(len(tg.sent),1)
            bcsrf=b.fields("/profile","/logout")["csrf"]
            self.assertEqual(b.request(route,{"csrf":bcsrf,"proof":foreign})[0],409)
            rejected=a.request(route,{**a.fields(ROOT,route),"proof":"0"*12})
            self.assertEqual(rejected[0],403)
            self.assertIn('data-restore-focus',rejected[2])
            self.assertIn('action="'+route+'"',rejected[2])
            self.assertEqual(self.db_one("SELECT count(*) FROM telegram_binding"),0)
            self.pump(tg,message(2,CHATS[1],"/tasks"),message(3,CHATS[0],"/today","group"))
            self.assertIn("введи код из личного чата",tg.sent[-1][1])
        with patch.dict(os.environ,ENV),clock.at(T+timedelta(seconds=20)):
            new=self.start(a)
            self.assertNotEqual(nonce,new)
            self.pump(tg,message(4,CHATS[0],"/start "+new))
            self.assertEqual(a.request(route,{**a.fields(ROOT,route),"proof":self.proof(tg,CHATS[0])})[0],303)
            self.assertEqual(self.db_one("SELECT count(*) FROM telegram_pair"),0)
            self.assertIsNone(self.db_one("SELECT chat_key FROM telegram_update WHERE update_id=3"))
            self.pump(tg,message(5,CHATS[0],"/today"))
            self.assertIn("Учебное демо",tg.sent[-1][1])
            self.assertNotIn("127.0.0.1",tg.sent[-1][1])
            before=len(tg.sent)
            self.pump(tg,message(5,CHATS[0],"/today"))
            self.assertEqual(len(tg.sent),before)
            self.assertEqual(tg.offsets[-1],6)
            self.pump(tg,message(6,CHATS[0],"/start "+new))
            self.assertIn("Кабинет уже подключён",tg.sent[-1][1])
            self.assertEqual(tg.menus[-1],telegram.keyboard("student"))
            self.assertNotIn(str(CHATS[0]),a.request(ROOT)[2])
            self.assertEqual(self.db_one("SELECT count(*) FROM telegram_outbox WHERE state='sent' AND body<>''"),0)

    def test_quiet_retry_unknown_delivery_and_revocation(self):
        a,b=self.client("student-a"),self.client("student-b")
        tg=FakeTransport()
        site="https://dean.example"
        config=TelegramConfig("polling",CONFIG.name,"",CONFIG.allowed,site)
        with patch.dict(os.environ,{**ENV,"DEAN_TELEGRAM_APP_URL":site}),clock.at(T):
            self.connect_account(a,tg,config=config)
            settings=ROOT+"/settings"
            self.assertEqual(a.request(settings,{**a.fields(ROOT,settings),"enabled":"1","task":"1"})[0],303)
            task=self.task_notice(T+timedelta(hours=16))
            previous=len(tg.sent)
            self.pump(tg,config=config)
            self.assertEqual(len(tg.sent),previous)
            self.assertEqual(self.db_one("SELECT count(*) FROM telegram_outbox WHERE kind='task' AND state='pending'"),1)
        later=T+timedelta(hours=9)
        with clock.at(later):
            tg.failures.append(TransportError("rate_limit",30))
            self.assertEqual(self.pump(tg,config=config)["delivery"]["retry"],1)
        with clock.at(later+timedelta(seconds=29)):
            self.assertEqual(self.pump(tg,config=config)["delivery"]["sent"],0)
        with clock.at(later+timedelta(seconds=31)):
            self.assertEqual(self.pump(tg,config=config)["delivery"]["sent"],1)
            self.assertIn("Приближается срок задачи",tg.sent[-1][1])
            self.assertNotIn("Внутренний текст",tg.sent[-1][1])
            target="/tasks/"+task
            self.assertIn(site+target,tg.sent[-1][1])
            self.assertEqual(a.request(target)[0],200)
            self.assertEqual(b.request(target)[0],404)
            status,headers,_=Client(self.app).request(target)
            self.assertEqual(status,303)
            self.assertIn("next=%2Ftasks%2F"+task,headers["Location"])
            self.task_notice(T+timedelta(hours=18))
            tg.failures.append(TransportError("uncertain"))
            self.assertEqual(self.pump(tg)["delivery"]["uncertain"],1)
            self.assertEqual(self.pump(tg)["delivery"]["sent"],0)
            doomed=self.task_notice(T+timedelta(hours=19))
            with closing(connect(self.path)) as db:
                telegram_worker.collect(db,CONFIG)
                with write(db):
                    db.execute("UPDATE task SET state='done' WHERE id=?",(doomed,))
                self.assertEqual(telegram_worker.send_batch(db,CONFIG,tg)["sent"],0)
            self.task_notice(T+timedelta(hours=20))
            with closing(connect(self.path)) as db:
                telegram_worker.collect(db,CONFIG)
            self.pump(tg,message(2,CHATS[0],"/stop"))
            self.assertIn("отключена",tg.sent[-1][1])
            self.assertEqual(tg.menus[-1],{"remove_keyboard":True})
            self.assertEqual(self.db_one("SELECT count(*) FROM telegram_binding WHERE revoked_at IS NULL"),0)
            self.assertEqual(self.db_one("SELECT count(*) FROM telegram_outbox WHERE state='pending'"),0)

    def test_last_rate_limited_attempt_pauses_the_rest_of_the_batch(self):
        tg = FakeTransport()
        with clock.at(T), closing(connect(self.path)) as db:
            self.pump(tg)
            with write(db):
                telegram.enqueue(db, CONFIG.name, CHATS[0], "generic", "first", "Первое сообщение")
                telegram.enqueue(db, CONFIG.name, CHATS[1], "generic", "second", "Второе сообщение")
                db.execute("UPDATE telegram_outbox SET attempts=? WHERE key='first'", (telegram_worker.MAX_ATTEMPTS - 1,))
            tg.failures.append(TransportError("rate_limit", 30))
            self.assertEqual(telegram_worker.send_batch(db, CONFIG, tg)["failed"], 1)
            self.assertEqual(tg.sent, [])
            self.assertEqual(telegram_worker.send_batch(db, CONFIG, tg)["sent"], 0)
            with clock.at(T + timedelta(seconds=31)):
                self.assertEqual(telegram_worker.send_batch(db, CONFIG, tg)["sent"], 1)
                self.assertEqual(tg.sent, [(CHATS[1], "Второе сообщение")])

    def test_in_flight_attempt_after_restart_is_uncertain(self):
        a=self.client("student-a")
        tg=FakeTransport()
        with patch.dict(os.environ,ENV),clock.at(T):
            self.connect_account(a,tg)
            choose(a,FIRST)
            self.pump(tg,message(2,CHATS[0],"/week"))
            self.assertIn("Данных вуза на эту неделю пока нет.",tg.sent[-1][1])
            with closing(connect(self.path)) as db, write(db):
                row=db.execute("SELECT * FROM telegram_binding").fetchone()
                telegram.enqueue(db,CONFIG.name,CHATS[0],"command","restart-test",
                                 "Не повторять автоматически",binding=row["id"])
            with closing(connect(self.path)) as db:
                row, lease=telegram_worker.claim(db,CONFIG)
                self.assertIsNotNone(lease)
                self.assertEqual(row["key"],"restart-test")
        with clock.at(T+telegram_worker.SEND_LEASE+timedelta(seconds=1)):
            self.assertEqual(self.pump(tg)["delivery"]["sent"],0)
            self.assertEqual(self.db_one("SELECT state FROM telegram_outbox WHERE key='restart-test'"),"uncertain")
            self.assertEqual(self.db_one("SELECT body FROM telegram_outbox WHERE key='restart-test'"),"")

    def test_student_menu_includes_personal_event_and_cabinet_link(self):
        a=self.client("student-a")
        tg=FakeTransport()
        site="https://dean.example"
        config=TelegramConfig("polling",CONFIG.name,"",CONFIG.allowed,site)
        with patch.dict(os.environ,{**ENV,"DEAN_TELEGRAM_APP_URL":site}), clock.at(T):
            self.connect_account(a,tg,config=config)
            self.assertIn([{"text":"✅ Задачи"},{"text":"📄 Заявки"}],tg.menus[-1]["keyboard"])
            path="/events/new"
            self.assertEqual(a.request(path,{**a.fields(path,path),"title":"Пара из личного календаря",
                "starts":"2026-11-19T12:00","ends":"2026-11-19T13:30","location":"А-301"})[0],303)
            self.pump(tg,message(2,CHATS[0],"Неделя"),config=config)
            self.assertIn("Пара из личного календаря · А-301",tg.sent[-1][1])
            self.assertIn(site+"/schedule",tg.sent[-1][1])
            self.pump(tg,message(3,CHATS[0],"/cabinet"),config=config)
            self.assertTrue(tg.sent[-1][1].endswith(site + "/"))
            self.assertEqual(a.request("/")[0], 200)

    def test_notification_routes_survive_login(self):
        site="https://dean.example"
        target="7"*32
        config=TelegramConfig(site=site)
        paths={"schedule":"/schedule", "public_change":"/schedule/public", "task":"/tasks",
               "application":"/requests", "work_result":"/work"}
        for kind, path in paths.items():
            with self.subTest(kind=kind):
                url=path+"/"+target
                text=telegram_worker.notice_message({"kind":kind,"target_id":target,
                                                     "event_key":f"{kind}:{target}:ready:1"},config)
                self.assertIn(site+url,text)
                status,headers,_=Client(self.app).request(url)
                self.assertEqual(status,303)
                self.assertEqual(headers["Location"],"/login?next="+url.replace("/","%2F"))

    def test_staff_menu_and_public_mode_only_accept_private_chats(self):
        staff=self.client("staff")
        tg=FakeTransport()
        chat=630003
        config=TelegramConfig("polling",CONFIG.name,"",frozenset(),"",True)
        with patch.dict(os.environ,{**ENV,"DEAN_TELEGRAM_PUBLIC":"1","DEAN_TELEGRAM_ALLOWED_CHAT_IDS":""}), clock.at(T):
            self.connect_account(staff,tg,chat,config)
            keys=tg.menus[-1]["keyboard"]
            self.assertIn([{"text":"📄 Очередь заявок"},{"text":"🏠 Кабинет"}],keys)
            self.assertNotIn("Задачи",str(keys))
            previous=len(tg.sent)
            self.pump(tg,message(2,chat,"Очередь заявок","group"),config=config)
            self.assertEqual(len(tg.sent),previous)
            self.pump(tg,message(3,chat,"Сегодня"),config=config)
            self.assertIn("Занятия твоих групп",tg.sent[-1][1])
            self.pump(tg,message(4,chat,"Очередь заявок"),config=config)
            self.assertIn("Ссылки появятся",tg.sent[-1][1])

    def test_transport_rejects_webhook_and_never_repeats_response_secret(self):
        def object_response(code,payload):
            class Response:
                status=code
                def __enter__(self):
                    return self
                def __exit__(self,*args):
                    return False
                def read(self,limit):
                    return json.dumps(payload).encode()
            return Response()
        canned=object_response(429,{"ok":False,"error_code":429,
                                     "description":"hidden-token-fixture",
                                     "parameters":{"retry_after":9}})
        class Connection:
            def request(self,*args):
                pass
            def getresponse(self):
                return canned
            def close(self):
                pass
        api=BotAPI(CONFIG,connect=lambda *args,**kwargs:Connection())
        with self.assertRaises(TransportError) as seen:
            api.send(CHATS[0],"Синтетика")
        self.assertEqual(seen.exception.retry_after,9)
        self.assertNotIn("hidden-token-fixture",str(seen.exception))
        canned=object_response(200,{"ok":True,"result":{"url":"https://example.test/existing-hook"}})
        with self.assertRaises(TransportError) as seen:
            api.preflight()
        self.assertEqual(seen.exception.code,"webhook_already_set")
        canned=object_response(409,{"ok":False,"error_code":409})
        with self.assertRaises(TransportError) as seen:
            api.updates(0)
        self.assertEqual(seen.exception.code,"poll_conflict")

    def test_private_token_file_is_only_loaded_for_worker(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ,ENV,clear=True):
            path=Path(folder)/"telegram.key"
            token="123456:local-test-token-01234567890"
            path.write_text(token+"\n",encoding="ascii")
            with patch.dict(os.environ,{"DEAN_TELEGRAM_BOT_TOKEN_FILE":str(path)}):
                worker=TelegramConfig.from_env(worker=True)
                self.assertEqual(worker.token,token)
                self.assertNotIn(token,repr(worker))
                self.assertEqual(TelegramConfig.from_env().token,"")
                path.unlink()
                self.assertEqual(TelegramConfig.from_env().name,CONFIG.name)
                with self.assertRaisesRegex(DomainError,"DEAN_TELEGRAM_BOT_TOKEN_FILE"):
                    TelegramConfig.from_env(worker=True)
                with patch.dict(os.environ,{"DEAN_TELEGRAM_BOT_TOKEN":token}):
                    self.assertEqual(TelegramConfig.from_env(worker=True).token,token)

    def test_read_only_preflight_discovers_and_checks_bot_name(self):
        for configured in ("",CONFIG.name,"other_fixture_bot"):
            with self.subTest(configured=configured):
                api=BotAPI(TelegramConfig(name=configured))
                replies=[{"url":""},{"username":CONFIG.name,"is_bot":True}]
                with patch.object(api,"call",side_effect=replies) as method:
                    if configured in ("",CONFIG.name):
                        self.assertEqual(api.preflight(),CONFIG.name)
                    else:
                        with self.assertRaisesRegex(TransportError,"bot_identity_mismatch"):
                            api.preflight()
                    self.assertEqual([call.args[0] for call in method.call_args_list],["getWebhookInfo","getMe"])
