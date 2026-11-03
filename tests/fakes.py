"""Scripted stand-in for an LLM client."""


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def complete(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r
