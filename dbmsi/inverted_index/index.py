from dbmsi.storage import RID
import re


def tokenize(filename: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", filename.lower())
class InvertedIndex:
    def __init__(self):
        self.postings: dict[str, list[RID]] = {}
    def add(self, rid: RID, filename: str) -> None:
        for term in tokenize(filename):
            if term not in self.postings:
                self.postings[term] = []
            self.postings[term].append(rid)
    def search(self, term: str) -> list[RID]:
        return self.postings.get(term.lower(), [])