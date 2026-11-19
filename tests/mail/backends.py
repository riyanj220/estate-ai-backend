import threading

from django.core.mail.backends.base import BaseEmailBackend

CANARY_ERROR = "smtp refused canary-rcpt@example.org"
sent_threads: list[str] = []
fail_next = {"on": False}


class RecordingBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if fail_next["on"]:
            raise ConnectionRefusedError(CANARY_ERROR)
        sent_threads.append(threading.current_thread().name)
        return len(email_messages)


class AlwaysFailingBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise ConnectionRefusedError(CANARY_ERROR)
