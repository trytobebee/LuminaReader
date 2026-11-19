"""Versioned, inspectable recipes learned from successful tool traces.

Only literal task wording with bound, indexed names is generalized. Selection is
an exact template match; ambiguity falls back to planning. Evidence validation is
not semantic proof: negative user feedback disables a recipe and its answer cache.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

VERSION = "reader-tools-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def normalize(text):
    return "".join(unicodedata.normalize("NFKC", text).split()).rstrip("?。.!！")


def substitute(value, bindings):
    if isinstance(value, str):
        for name, replacement in bindings.items():
            value = value.replace("{{" + name + "}}", replacement)
        return value
    if isinstance(value, list):
        return [substitute(item, bindings) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, bindings) for key, item in value.items()}
    return value


class WorkflowStore:
    def __init__(self, directory, identity, revision=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.identity = identity
        self.revision = revision
        self.path = self.directory / "workflows.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS recipes (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                    successes TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tools (key TEXT PRIMARY KEY, result TEXT NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def cached_tool(self, key):
        with self.connect() as conn:
            row = conn.execute("SELECT result FROM tools WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_tool(self, key, result):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO tools VALUES (?,?)", (key, canonical(result)))

    def forget_tools(self, keys):
        with self.connect() as conn:
            conn.executemany("DELETE FROM tools WHERE key=?", ((key,) for key in keys))

    def list(self, active_only=False):
        with self.connect() as conn:
            rows = conn.execute("SELECT id,payload,status,successes,hits FROM recipes ORDER BY id").fetchall()
        return [{**json.loads(payload), "id": key, "status": status,
                 "success_count": len(json.loads(successes)), "hits": hits}
                for key, payload, status, successes, hits in rows
                if not active_only or status == "active"]

    def match(self, question, context, known_names):
        matches = []
        for recipe in self.list(active_only=True):
            if recipe["context"] not in {context,"independent"} or recipe["identity"] != self.identity:
                continue
            if recipe.get("source_revision") not in (None,self.revision):
                continue
            template = recipe["template"]
            pattern = ""
            position = 0
            for token in re.finditer(r"\{\{(entity_\d+)\}\}", template):
                pattern += re.escape(template[position:token.start()])
                pattern += f"(?P<{token[1]}>.+?)"
                position = token.end()
            pattern += re.escape(template[position:])
            match = re.fullmatch(pattern, normalize(question))
            if match and all(name in known_names for name in match.groupdict().values()):
                matches.append((recipe, match.groupdict()))
        return matches[0] if len(matches) == 1 else None

    def select(self, recipe_id, context, bindings, known_names):
        for recipe in self.list(active_only=True):
            if recipe["id"]!=recipe_id or recipe["context"] not in {context,"independent"} or recipe["identity"]!=self.identity:
                continue
            if recipe.get("source_revision") not in (None,self.revision):
                continue
            expected=set(re.findall(r"\{\{(entity_\d+)\}\}",recipe["template"]))
            if not isinstance(bindings,dict) or set(bindings)!=expected or any(value not in known_names for value in bindings.values()):
                raise ValueError("流程参数须绑定到已识别的人物，且不得缺失或多出参数")
            return recipe
        raise ValueError("流程不存在、未启用或与当前上下文不符")

    def suggestions(self, question, context):
        from .qa import terms
        ranked=[]
        words=terms(question)
        for recipe in self.list(active_only=True):
            if recipe["context"] in {context,"independent"} and recipe["identity"]==self.identity and recipe.get("source_revision") in (None,self.revision):
                ranked.append((len(words & terms(recipe["template"])),recipe))
        ranked.sort(key=lambda item:(-item[0],item[1]["id"]))
        return [{"id":row["id"],"task_template":row["template"],
                 "parameters":sorted(set(re.findall(r"\{\{(entity_\d+)\}\}",row["template"]))),
                 "steps":[step["tool"] for step in row["steps"]]} for _,row in ranked[:8]]

    def learn(self, question, context, names, steps, coverage, required_chapters, context_dependencies=None):
        if not steps or any(step["tool"] not in {"catalog", "search", "read", "scan", "extract", "aggregate"} for step in steps):
            return None
        # A parameterized recipe must derive source IDs from prior tool results,
        # never replay chapter/paragraph IDs copied from an earlier answer.
        parameters = sorted({name for name in names if name in question}, key=lambda name: (-len(name), name))
        parameters = [name for name in parameters if not any(name != other and name in other for other in parameters)]
        parameters.sort(key=question.index)
        if len(parameters) > 6:
            return None
        if parameters:
            if required_chapters:
                return None
            for step in steps:
                args = step["arguments"]
                if step["tool"]=="scan" and any(type(args.get(field)) is int for field in ("start","end")):
                    return None
                sensitive = {"read": "chapter_ids", "extract": "passage_ids", "aggregate": "records"}.get(step["tool"])
                if sensitive and not (isinstance(args.get(sensitive), str) and args[sensitive].startswith("$")):
                    return None
        template = normalize(question)
        aliases = {step["id"]:"step"+str(index) for index,step in enumerate(steps)}
        def rename(value):
            if isinstance(value,str) and value.startswith("$"):
                head,*tail = value[1:].split(".")
                return "$"+aliases.get(head,head)+("."+".".join(tail) if tail else "")
            if isinstance(value,list):
                return [rename(item) for item in value]
            if isinstance(value,dict):
                return {key:rename(item) for key,item in value.items()}
            return value
        steps = [{"id":aliases[step["id"]],"tool":step["tool"],"arguments":rename(step["arguments"])} for step in steps]
        bindings = {}
        for index, name in enumerate(parameters):
            key = "entity_" + str(index)
            bindings[key] = name
            template = template.replace(normalize(name), "{{" + key + "}}")
        # Replace names as JSON strings' contents without interpreting model code.
        def parameterize(value):
            if isinstance(value, str):
                for key, name in bindings.items():
                    value = value.replace(name, "{{" + key + "}}")
                return value
            if isinstance(value, list):
                return [parameterize(item) for item in value]
            if isinstance(value, dict):
                return {key: parameterize(item) for key, item in value.items()}
            return value
        templated_steps=parameterize(steps)
        independent=(bool(bindings) and context_dependencies==[] and any(
            step["tool"]=="search" and "{{entity_" in str(step["arguments"].get("query","")) for step in templated_steps)
            and not any(step["tool"]=="catalog" for step in templated_steps))
        payload = {"name": normalize(question)[:60], "template": template, "context": "independent" if independent else context,
                   "identity": self.identity, "steps": templated_steps,
                   "coverage": coverage, "required_chapters": required_chapters}
        literal_sources=bool(required_chapters) or any(
            (step["tool"] in {"read","extract"} and not isinstance(step["arguments"].get("chapter_ids" if step["tool"]=="read" else "passage_ids"),str))
            or (step["tool"] in {"scan","catalog"} and any(type(step["arguments"].get(field)) is int for field in ("start","end")))
            for step in steps)
        payload["source_revision"]=self.revision if literal_sources else None
        # Name is explanatory and must not prevent two entity bindings sharing a recipe.
        payload["name"] = template[:60]
        key = digest([VERSION, payload])
        sample = digest(bindings if bindings else normalize(question))
        with self.connect() as conn:
            old = conn.execute("SELECT status,successes FROM recipes WHERE id=?", (key,)).fetchone()
            successes = set(json.loads(old[1])) if old else set()
            successes.add(sample)
            status = "disabled" if old and old[0] == "disabled" else ("active" if len(successes) >= 2 else "candidate")
            conn.execute("INSERT INTO recipes(id,payload,status,successes) VALUES (?,?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET successes=excluded.successes,status=excluded.status",
                         (key, canonical(payload), status, canonical(sorted(successes))))
        return {"id": key, "status": status, "success_count": len(successes)}

    def feedback(self, recipe_id, helpful):
        with self.connect() as conn:
            conn.execute("UPDATE recipes SET status=? WHERE id=?", ("active" if helpful else "disabled", recipe_id))

    def hit(self, recipe_id):
        with self.connect() as conn:
            conn.execute("UPDATE recipes SET hits=hits+1 WHERE id=?", (recipe_id,))
