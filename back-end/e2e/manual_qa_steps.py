"""Worker Q&A and access revocation steps (M11-M13) of the manual E2E."""

from e2e.manual_scenario import _check, _key, speech
from tests import media_samples


class QaSteps:
    """Mixin for `Scenario`, using the shared manual helpers and actors."""

    def step_m11_qa_answered(self):
        worker, version = self.state["worker-a"], self.state["version"]
        conversation = worker.call("POST", self.manual_url("/qa/conversations"),
                                   json={}, key=_key(), expect=201).json()
        path = self.manual_url(f"/qa/conversations/{conversation['id']}/questions")
        started = worker.call("POST", path, key=_key(), expect=202, json={
            "kind": "TEXT", "text": "포스 마감은 어떻게 해요?", "transcriptionId": None,
            "imageMediaIds": []}).json()
        ready = self.poll(worker, f"{path}/{started['id']}", lambda q: q["status"] != "RUNNING",
                          "Q&A answer")
        _check(ready["status"] == "READY", f"the question is answered: {ready.get('error')}")
        _check(ready["manualVersionId"] == version, "the answer keeps the published version")
        answer = ready["answer"]
        if self.live:
            _check(answer["outcome"] in ("ANSWERED", "NEEDS_OWNER"), "a valid live answer outcome")
        else:
            _check(answer["outcome"] == "ANSWERED", "the fake answers the POS question")
        if answer["outcome"] == "ANSWERED":
            _check(len(answer["citations"]) >= 1, "an answer has manual citations")
            for citation in answer["citations"]:
                _check(citation["versionId"] == version, "the citation belongs to the answer's version")
                detail = worker.call(
                    "GET", self.manual_url(f"/published/sections/{citation['sectionId']}"),
                    params={"expectedVersionId": version}, expect=200)
                section = detail.json()["section"]
                _check(section["id"] == citation["sectionId"], "the cited section is published", detail)
                _check(section["title"] == citation["sectionTitle"], "the citation keeps the section title", detail)
                if not self.live:
                    _check(citation["sectionId"] == self.state["pos_section"], "the fake cites the POS section")
                    steps = section["steps"]
                    _check(len(steps) >= 1, "the POS section has steps", detail)
                    # Public citations omit step IDs; the fake cites every step of this section.
                    excerpt = "\n".join(step["instruction"] for step in steps)
                    if len(excerpt) > 1000:
                        excerpt = excerpt[:999].rstrip() + "…"
                    _check(citation["excerpt"].startswith(steps[0]["instruction"]),
                           "the excerpt starts with the first published step", detail)
                    _check(citation["excerpt"] == excerpt, "the server quotes the published steps verbatim", detail)
        else:
            _check(answer["citations"] == [], "an owner-needed answer has no citations")
        self.state.update(qa_conversation=conversation["id"], qa_first_question=ready)

    def step_m12_qa_needs_owner_and_voice(self):
        worker = self.state["worker-a"]
        conversation_path = self.manual_url(f"/qa/conversations/{self.state['qa_conversation']}")
        path = f"{conversation_path}/questions"
        started = worker.call("POST", path, key=_key(), expect=202, json={
            "kind": "TEXT", "text": "주차는 어디에 해요?", "transcriptionId": None,
            "imageMediaIds": []}).json()
        parking = self.poll(worker, f"{path}/{started['id']}", lambda q: q["status"] != "RUNNING",
                            "parking question")
        _check(parking["status"] == "READY", f"the parking question completed: {parking.get('error')}")
        if not self.live:
            _check(parking["answer"]["outcome"] == "NEEDS_OWNER" and parking["answer"]["citations"] == [],
                   "parking needs the owner and has no citations")

        # Wait for each question before asking another in the same conversation.
        if self.live:
            audio, filename, mime = speech("포스 마감은 어떻게 해요?"), "a.m4a", "audio/mp4"
        else:
            audio, filename, mime = media_samples.wav_seconds(3), "a.wav", "audio/wav"
        uploaded = worker.call("POST", self.manual_url("/qa/media"), key=_key(), expect=201,
                               data={"purpose": "QUESTION_AUDIO"},
                               files={"file": (filename, audio, mime)}).json()
        _check(uploaded["purpose"] == "QUESTION_AUDIO" and uploaded["mimeType"] == mime,
               "the question recording is stored")
        started = worker.call("POST", self.manual_url("/qa/transcriptions"), key=_key(), expect=202,
                              json={"mediaId": uploaded["id"]}).json()
        transcript = self.poll(worker, self.manual_url(f"/qa/transcriptions/{started['id']}"),
                               lambda t: t["status"] != "RUNNING", "question transcription")
        _check(transcript["status"] == "READY", f"the recording is transcribed: {transcript.get('error')}")
        if self.live:
            _check(bool(transcript["text"].strip()), "the live transcript is non-empty")
        else:
            _check(transcript["text"] == "테스트 전사 결과예요.", "the fake question transcript")
        started = worker.call("POST", path, key=_key(), expect=202, json={
            "kind": "VOICE", "text": None, "transcriptionId": transcript["id"], "imageMediaIds": []}).json()
        voice = self.poll(worker, f"{path}/{started['id']}", lambda q: q["status"] != "RUNNING",
                          "voice question")
        _check(voice["status"] == "READY", f"the voice question completed: {voice.get('error')}")
        _check(voice["text"] == transcript["text"], "the voice question uses the ready transcript")
        if not self.live:
            _check(voice["answer"]["outcome"] == "NEEDS_OWNER" and voice["answer"]["citations"] == [],
                   "the fake transcript question needs the owner")

        restored = worker.call("GET", conversation_path, expect=200)
        turns = restored.json()["turns"]
        _check(len(turns) == 3 and [turn["sequence"] for turn in turns] == [1, 2, 3],
               "the conversation restores three questions in ask order", restored)
        _check(turns == [self.state["qa_first_question"], parking, voice],
               "restored questions keep their text, final statuses and answers", restored)

    def step_m13_access_revoked(self):
        worker = self.state["worker-c"]
        conversation = worker.call("POST", self.manual_url("/qa/conversations"),
                                   json={}, key=_key(), expect=201).json()
        conversation_path = self.manual_url(f"/qa/conversations/{conversation['id']}")
        body = {"kind": "TEXT", "text": "주차는 어디에 해요?", "transcriptionId": None, "imageMediaIds": []}
        started = worker.call("POST", f"{conversation_path}/questions", json=body,
                              key=_key(), expect=202).json()
        ready = self.poll(worker, f"{conversation_path}/questions/{started['id']}",
                          lambda q: q["status"] != "RUNNING", "worker C's question history")
        _check(ready["status"] == "READY", f"worker C has completed history: {ready.get('error')}")

        self.state["owner"].call(
            "DELETE", f"/api/stores/{self.state['store_id']}/workers/{self.state['worker-c-id']}/access",
            write=True, expect=204)
        hidden = worker.call("GET", conversation_path, expect=404)
        _check(hidden.json()["code"] == "RESOURCE_NOT_FOUND", "revoked access hides existing Q&A history", hidden)
        refused = worker.call("POST", f"{conversation_path}/questions", json=body, key=_key(), expect=404)
        _check(refused.json()["code"] == "RESOURCE_NOT_FOUND", "revoked access refuses questions", refused)
        refused = worker.call("POST", self.manual_url("/qa/conversations"), json={}, key=_key(), expect=404)
        _check(refused.json()["code"] == "RESOURCE_NOT_FOUND", "revoked access refuses new conversations", refused)
        hidden = worker.call("GET", self.manual_url("/published"), expect=404)
        _check(hidden.json()["code"] == "STORE_NOT_FOUND", "revoked access hides the published manual", hidden)
        for name in ("worker-a", "worker-d"):
            published = self.state[name].call("GET", self.manual_url("/published"), expect=200)
            _check(published.json()["versionId"] == self.state["version"],
                   f"{name} can still read the published manual", published)
        self.state["manual_readers_after_m13"] = ("worker-a", "worker-d")
