#!/usr/bin/env python3
"""Checks a repository against the conventions its conventions.yaml names.

Usage: conventions.py <config.json> [<repository root>] [--only r1,r2] [--skip r1,r2]

--only runs just the named rules, --skip leaves them out: the pr check runs
the rules every repository has (--only todo-issue,markdown-links), the
conventions job of a pipeline the rest.

The workflow converts conventions.yaml to JSON first (yq), so that this script
needs nothing beyond the Python standard library. The config:

  rules:       <rule>: {level: error | warn | off, ...parameters}; the
               rules in DEFAULT_ON run unless they are set to off, the others
               only when they are named
  units:       globs of the unit files (terragrunt.hcl) for the terragrunt rules
  exceptions:  [{rule, path (a glob), match (optional text on the line),
                 reason, review (YYYY-MM-DD)}] - an exception without a reason
                 is itself an error; the review date is checked by
                 exception_review.py, which warns once it has passed

Every violation becomes a GitHub annotation on its file and line, and a row
in the job summary. The exit code is 1 when an error-level violation is not
covered by an exception, or when the config itself is wrong.

The rules read HCL through the small parser below: blocks, attributes,
strings, heredocs and comments, enough to know what is a `default` inside a
`variable` block and what is a comment above an attribute. Expressions are
kept as tokens, not evaluated.
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse

# --------------------------------------------------------------------------
# HCL tokens and structure

GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
RE_HEREDOC = re.compile(r"<<-?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\n")


class Token:
    __slots__ = ("kind", "value", "line")

    def __init__(self, kind, value, line):
        self.kind, self.value, self.line = kind, value, line

    def __repr__(self):
        return f"{self.kind}:{self.value!r}@{self.line}"


def tokenize(text: str):
    """Tokens plus the set of lines that carry a comment."""
    tokens, comments = [], set()
    i, line, n = 0, 1, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            tokens.append(Token("nl", "\n", line))
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#" or text.startswith("//", i):
            comments.add(line)
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            for k in range(text.count("\n", i, end) + 1):
                comments.add(line + k)
            line += text.count("\n", i, end)
            i = end
        elif c == '"':
            start_line = line
            i, value = read_string(text, i + 1)
            line += value.count("\n")
            tokens.append(Token("str", value, start_line))
        elif (m := RE_HEREDOC.match(text, i)):
            marker, start_line = m.group(1), line
            body_start = m.end()
            j, body = body_start, []
            line += 1
            while j < n:
                end = text.find("\n", j)
                end = n if end < 0 else end
                current = text[j:end]
                if current.strip() == marker:
                    j = end
                    break
                body.append(current)
                line += 1
                j = end + 1
            tokens.append(Token("str", "\n".join(body), start_line))
            i = j
        elif (m := RE_IDENT.match(text, i)):
            tokens.append(Token("ident", m.group(0), line))
            i = m.end()
        else:
            tokens.append(Token("punct", c, line))
            i += 1
    tokens.append(Token("nl", "\n", line))
    return tokens, comments


def read_string(text: str, i: int):
    """The raw content of a quoted string from i to its closing quote."""
    out, n, depth = [], len(text), 0
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if depth == 0 and c == '"':
            return i + 1, "".join(out)
        if text.startswith(("${", "%{"), i):
            depth += 1
            out.append(text[i:i + 2])
            i += 2
            continue
        if depth and c == "{":
            depth += 1
        elif depth and c == "}":
            depth -= 1
        elif depth and c == '"':
            j, inner = read_string(text, i + 1)
            out.append('"' + inner + '"')
            i = j
            continue
        out.append(c)
        i += 1
    return n, "".join(out)


class Block:
    def __init__(self, kind, labels, line):
        self.kind, self.labels, self.line = kind, labels, line
        self.attrs, self.blocks = {}, []

    def find(self, kind):
        return [b for b in self.blocks if b.kind == kind]


class Attr:
    def __init__(self, name, line, value):
        self.name, self.line, self.value = name, line, value

    def string(self):
        """The value when it is one plain string, else None."""
        if len(self.value) == 1 and self.value[0].kind == "str":
            return self.value[0].value
        return None

    def text(self):
        return "".join(t.value if t.kind != "str" else f'"{t.value}"' for t in self.value)


OPEN, CLOSE = "([{", ")]}"


def parse(tokens):
    root = Block("<file>", [], 1)
    body(tokens, 0, root)
    return root


def body(tokens, i, block):
    """Fills block from tokens[i] up to its closing brace; returns the index after."""
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t.kind == "nl":
            i += 1
            continue
        if t.kind == "punct" and t.value == "}":
            return i + 1
        if t.kind == "ident":
            j = i + 1
            if j < n and tokens[j].kind == "punct" and tokens[j].value in "=:":
                start, depth, k = j + 1, 0, j + 1
                while k < n:
                    tk = tokens[k]
                    if tk.kind == "punct" and tk.value in OPEN:
                        depth += 1
                    elif tk.kind == "punct" and tk.value in CLOSE:
                        if depth == 0:
                            break
                        depth -= 1
                    elif tk.kind == "nl" and depth == 0:
                        break
                    k += 1
                value = [x for x in tokens[start:k] if x.kind != "nl"]
                block.attrs[t.value] = Attr(t.value, t.line, value)
                i = k
                continue
            labels = []
            while j < n and tokens[j].kind in ("str", "ident"):
                labels.append(tokens[j].value)
                j += 1
            if j < n and tokens[j].kind == "punct" and tokens[j].value == "{":
                child = Block(t.value, labels, t.line)
                block.blocks.append(child)
                i = body(tokens, j + 1, child)
                continue
        i += 1
    return i


def literal(tokens):
    """A literal value (string, number, bool, list, object) from tokens, or None."""
    try:
        value, rest = _literal(tokens, 0)
    except (IndexError, ValueError):
        return None
    return value if rest == len(tokens) else None


def _literal(tokens, i):
    t = tokens[i]
    if t.kind == "str":
        return t.value, i + 1
    if t.kind == "ident" and t.value in ("true", "false", "null"):
        return {"true": True, "false": False, "null": None}[t.value], i + 1
    if t.kind == "punct" and t.value == "[":
        out, i = [], i + 1
        while not (tokens[i].kind == "punct" and tokens[i].value == "]"):
            v, i = _literal(tokens, i)
            out.append(v)
            if tokens[i].kind == "punct" and tokens[i].value == ",":
                i += 1
        return out, i + 1
    if t.kind == "punct" and t.value == "{":
        out, i = {}, i + 1
        while not (tokens[i].kind == "punct" and tokens[i].value == "}"):
            key = tokens[i].value
            if tokens[i + 1].kind != "punct" or tokens[i + 1].value not in "=:":
                raise ValueError("not an object")
            v, i = _literal(tokens, i + 2)
            out[key] = v
            if tokens[i].kind == "punct" and tokens[i].value == ",":
                i += 1
        return out, i + 1
    raise ValueError("not a literal")


def load(path):
    with open(path, encoding="utf-8") as fh:
        tokens, comments = tokenize(fh.read())
    return parse(tokens), tokens, comments


# --------------------------------------------------------------------------
# The repository

def glob_re(pattern: str):
    """A glob where * stays within a directory and **/ spans any number of them."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def matches(path: str, *patterns) -> bool:
    return any(glob_re(p).match(path) for p in patterns)


class Repo:
    def __init__(self, root):
        self.root = root
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                             text=True, check=True).stdout
        self.files = sorted(f for f in out.splitlines() if os.path.isfile(os.path.join(root, f)))
        self._cache = {}

    def glob(self, *patterns):
        return [f for f in self.files if matches(f, *patterns)]

    def hcl(self, path):
        if path not in self._cache:
            self._cache[path] = load(os.path.join(self.root, path))
        return self._cache[path]

    def exists(self, path):
        return path in self.files


class Report:
    def __init__(self):
        self.items = []

    def add(self, rule, path, line, message):
        self.items.append({"rule": rule, "path": path, "line": line, "message": message})


# --------------------------------------------------------------------------
# Rules. Each takes (repo, params, units, report) and adds what it finds.

def rule_variable_no_default(repo, params, units, report):
    """Module variables have no default; `optional(type, default)` counts too."""
    for path in repo.glob(*params.get("files", ["**/*.tf"])):
        root, _, _ = repo.hcl(path)
        for var in root.find("variable"):
            name = var.labels[0] if var.labels else "?"
            if "default" in var.attrs:
                report.add("variable-no-default", path, var.attrs["default"].line,
                           f'variable "{name}" has a default')
            if "type" in var.attrs:
                for line in optional_defaults(var.attrs["type"].value):
                    report.add("variable-no-default", path, line,
                               f'variable "{name}": optional() with a default')


def optional_defaults(tokens):
    """Lines of optional(...) calls that pass a second argument."""
    out = []
    for k, t in enumerate(tokens):
        if t.kind == "ident" and t.value == "optional" and k + 1 < len(tokens) \
                and tokens[k + 1].value == "(":
            depth, j = 0, k + 1
            while j < len(tokens):
                v = tokens[j]
                if v.kind == "punct" and v.value in OPEN:
                    depth += 1
                elif v.kind == "punct" and v.value in CLOSE:
                    depth -= 1
                    if depth == 0:
                        break
                elif v.kind == "punct" and v.value == "," and depth == 1:
                    out.append(t.line)
                    break
                j += 1
    return out


def rule_variable_documented(repo, params, units, report):
    """Every module variable says what it is (description) and what it takes (type)."""
    for path in repo.glob(*params.get("files", ["**/*.tf"])):
        root, _, _ = repo.hcl(path)
        for var in root.find("variable"):
            missing = [a for a in ("description", "type") if a not in var.attrs]
            if missing:
                report.add("variable-documented", path, var.line,
                           f'variable "{var.labels[0] if var.labels else "?"}" has no '
                           + " and no ".join(missing))


RE_MODULE = re.compile(r"//([^?]+)\?")


def unit_includes(repo, path):
    """The repository paths of the _common/units/ files a unit includes."""
    root, _, _ = repo.hcl(path)
    out = []
    for inc in root.find("include"):
        if "path" not in inc.attrs:
            continue
        m = re.search(r"_common/units/[\w.-]+\.hcl", inc.attrs["path"].text())
        if m:
            out.append(m.group(0))
    return out


def module_of(repo, path):
    """(module path, line, file) of the module a unit runs, its own or its _common/units/ file's."""
    for f in [path] + unit_includes(repo, path):
        if not repo.exists(f):
            continue
        root, _, _ = repo.hcl(f)
        for b in root.find("terraform"):
            if "source" in b.attrs and (m := RE_MODULE.search(b.attrs["source"].text())):
                return m.group(1).strip("/"), b.attrs["source"].line, f
    return None, None, None


def rule_module_source(repo, params, units, report):
    """A unit takes its module from git, through modules and modules_ref.

    The source is in the unit, or in the _common/units/<module>.hcl it includes.
    """
    pattern = re.compile(params.get(
        "pattern", r"^\$\{include\.root\.locals\.modules\}//[^?]+\?ref=\$\{include\.root\.locals\.modules_ref\}$"))
    common = re.compile(params.get(
        "common_pattern", r"^\$\{[A-Za-z_.]+\.modules\}//[^?]+\?ref=\$\{[A-Za-z_.]+\.modules_ref\}$"))
    for path in units:
        root, _, _ = repo.hcl(path)
        sources = [b.attrs["source"] for b in root.find("terraform") if "source" in b.attrs]
        included = [f for f in unit_includes(repo, path) if repo.exists(f)]
        if not sources and not any("source" in b.attrs for f in included
                                   for b in repo.hcl(f)[0].find("terraform")):
            report.add("module-source", path, 1,
                       "the unit names no terraform source, itself or in a _common/units/ file")
        for attr in sources:
            value = attr.string()
            if value is None or not pattern.match(value):
                report.add("module-source", path, attr.line,
                           f"source {attr.text()} is not the module from git with modules_ref")
    for path in repo.glob("_common/units/*.hcl"):
        root, _, _ = repo.hcl(path)
        for b in root.find("terraform"):
            attr = b.attrs.get("source")
            if attr and (attr.string() is None or not common.match(attr.string())):
                report.add("module-source", path, attr.line,
                           f"source {attr.text()} is not the module from git with modules_ref")


def shared_dirs(params):
    """Where dependency blocks may live: dir (or dirs) and _common/units."""
    dirs = params.get("dirs") or [params.get("dir", "_common/deps")]
    return list(dict.fromkeys([d.rstrip("/") for d in dirs] + ["_common/units"]))


def rule_dependencies_in_common(repo, params, units, report):
    """A dependency is declared once, in a shared file, with its mocks."""
    where = " or ".join(f"{d}/" for d in shared_dirs(params))
    for path in units:
        root, _, _ = repo.hcl(path)
        for dep in root.find("dependency"):
            report.add("dependencies-in-common", path, dep.line,
                       f'dependency "{dep.labels[0] if dep.labels else "?"}" belongs in {where}')


def rule_mock_outputs(repo, params, units, report):
    """Every dependency has mocks, and the mocks never reach an apply or a destroy."""
    forbidden = set(params.get("forbidden_commands", ["apply", "destroy"]))
    mocks = {}
    if params.get("mocks_file") and repo.exists(params["mocks_file"]):
        mroot, _, _ = repo.hcl(params["mocks_file"])
        for loc in mroot.find("locals"):
            for name, attr in loc.attrs.items():
                mocks[name] = literal(attr.value)
    for path in repo.glob(*(f"{d}/*.hcl" for d in shared_dirs(params))):
        root, _, _ = repo.hcl(path)
        for dep in root.find("dependency"):
            label = dep.labels[0] if dep.labels else "?"
            for needed in ("mock_outputs", "mock_outputs_allowed_terraform_commands"):
                if needed not in dep.attrs:
                    report.add("mock-outputs", path, dep.line, f'dependency "{label}" has no {needed}')
            attr = dep.attrs.get("mock_outputs_allowed_terraform_commands")
            if not attr:
                continue
            commands = literal(attr.value)
            ref = attr.text()
            if commands is None and (m := re.fullmatch(r"local\.mocks\.(\w+)", ref)):
                commands = mocks.get(m.group(1))
            if not isinstance(commands, list):
                report.add("mock-outputs", path, attr.line,
                           f"cannot tell which commands {ref} allows")
            elif bad := sorted(forbidden & set(commands)):
                report.add("mock-outputs", path, attr.line,
                           f'dependency "{label}" allows its mocks in {", ".join(bad)}')


def rule_tags_required(repo, params, units, report):
    """Context files carry the tags their level owns, with an allowed value."""
    allowed = {}
    if params.get("allowed_values") and repo.exists(params["allowed_values"]):
        aroot, _, _ = repo.hcl(params["allowed_values"])
        for loc in aroot.find("locals"):
            if "allowed" in loc.attrs:
                allowed = literal(loc.attrs["allowed"].value) or {}
    for name, keys in (params.get("files") or {}).items():
        for path in repo.glob(f"**/{name}"):
            root, _, _ = repo.hcl(path)
            attrs = {}
            for loc in root.find("locals"):
                attrs.update(loc.attrs)
            for key in keys:
                if key not in attrs:
                    report.add("tags-required", path, 1, f"{name} sets no {key}")
                    continue
                value = attrs[key].string()
                if key in allowed and value not in allowed[key]:
                    report.add("tags-required", path, attrs[key].line,
                               f"{key} = {attrs[key].text()} is not one of {', '.join(allowed[key])}")


def rule_no_hardcoded_ids(repo, params, units, report):
    """Units take subscription and tenant IDs from the context, never written out."""
    known = set()
    for path in repo.glob(*params.get("id_files", ["account.hcl", "**/subscription.hcl"])):
        _, tokens, _ = repo.hcl(path)
        known |= {t.value.lower() for t in tokens if t.kind == "str" and re.fullmatch(GUID, t.value)}
    sub = re.compile(r"/subscriptions/" + GUID, re.I)
    for path in units:
        _, tokens, _ = repo.hcl(path)
        for t in tokens:
            if t.kind != "str":
                continue
            if sub.search(t.value):
                report.add("no-hardcoded-ids", path, t.line, "a resource ID with a subscription written out")
            elif any(g.lower() in known for g in re.findall(GUID, t.value)):
                report.add("no-hardcoded-ids", path, t.line,
                           "a subscription or tenant ID written out instead of taken from the context")


def rule_no_hardcoded_location(repo, params, units, report):
    """The region comes from region.hcl."""
    regions = {r.lower() for r in params.get("locations", [])}
    for path in units:
        _, tokens, _ = repo.hcl(path)
        for t in tokens:
            if t.kind == "str" and t.value.lower() in regions:
                report.add("no-hardcoded-location", path, t.line,
                           f'region "{t.value}" written out instead of taken from region.hcl')


def rule_no_envcommon(repo, params, units, report):
    """No _envcommon: every unit carries its own inputs."""
    for path in repo.files:
        if "_envcommon/" in f"/{path}" or path.startswith("_envcommon/"):
            report.add("no-envcommon", path, 1, "_envcommon is not used here")


def rule_lock_none_needs_reason(repo, params, units, report):
    """An unlocked resource in a resource group of high criticality says why."""
    levels = set(params.get("criticality", ["high"]))
    for path in units:
        root, _, comments = repo.hcl(path)
        attr = None
        for item in [root] + root.blocks:
            if "inputs" in item.attrs:
                obj = item.attrs["inputs"]
                for k, t in enumerate(obj.value):
                    if t.kind == "ident" and t.value == "lock" and k + 2 < len(obj.value) \
                            and obj.value[k + 1].value in "=:" and obj.value[k + 2].kind == "str":
                        attr = (t.line, obj.value[k + 2].value)
        if not attr or attr[1] != "none":
            continue
        rg = nearest(repo, os.path.dirname(path), "rg.hcl")
        if not rg:
            continue
        rroot, _, _ = repo.hcl(rg)
        crit = None
        for loc in rroot.find("locals"):
            if "criticality" in loc.attrs:
                crit = loc.attrs["criticality"].string()
        line = attr[0]
        if crit in levels and line not in comments and (line - 1) not in comments:
            report.add("lock-none-needs-reason", path, line,
                       f'lock = "none" in a resource group of criticality {crit}, with no comment saying why')


# --------------------------------------------------------------------------
# The shape of a live repository (code/terragrunt.md of governance)

def unit_dirs(repo):
    return {os.path.dirname(f) for f in repo.files if os.path.basename(f) == "terragrunt.hcl"}


def owning_unit(path, dirs):
    d = os.path.dirname(path)
    while d:
        if d in dirs:
            return d
        d = os.path.dirname(d)
    return None


def rule_unit_assets(repo, params, units, report):
    """A unit holds HCL; any other file only from the allowlist, and read by its terragrunt.hcl."""
    allowed = set(params.get("allowed", ["definition.json"]))
    roots = params.get("paths", ["infrastructure/**"])
    dirs = unit_dirs(repo)
    for path in repo.files:
        if path.endswith(".hcl") or os.path.basename(path) == ".terraform.lock.hcl":
            continue
        unit = owning_unit(path, dirs)
        if unit is None and not matches(path, *roots):
            continue
        name = os.path.basename(path)
        if unit is None or os.path.dirname(path) != unit or name not in allowed:
            report.add("unit-assets", path, 1,
                       f"{name} is not HCL: only {', '.join(sorted(allowed)) or 'nothing'} may sit "
                       "next to a terragrunt.hcl; write the rest in HCL (a heredoc, inline KQL)")
            continue
        text = "\n".join(text_lines(repo, f"{unit}/terragrunt.hcl") or [])
        if name not in text:
            report.add("unit-assets", path, 1, f"{name} is not read by {unit}/terragrunt.hcl")


SCOPE_CONTEXT = {"mg": "mg.hcl", "sub": "subscription.hcl", "region": "region.hcl", "rg": "rg.hcl"}
UNIT_FILES = {"terragrunt.hcl", ".terraform.lock.hcl"}


def rule_scope_layout(repo, params, units, report):
    """Each level of the hierarchy holds its context file, its own unit and the next level only."""
    root = params.get("root", "infrastructure").strip("/")
    dirs = unit_dirs(repo)
    children, files = {}, {}
    for f in repo.files:
        if not f.startswith(root + "/"):
            continue
        d = os.path.dirname(f)
        files.setdefault(d, []).append(os.path.basename(f))
        while d != root and d:
            parent = os.path.dirname(d)
            children.setdefault(parent, set()).add(os.path.basename(d))
            d = parent

    def level(d):
        name, parent = os.path.basename(d), os.path.basename(os.path.dirname(d))
        if name == "global":
            return "global"
        for prefix in ("mg", "sub", "rg"):
            if name.startswith(prefix + "-"):
                return prefix
        if parent.startswith("sub-"):
            return "region"
        if "device.hcl" in files.get(d, []):
            return "device"
        return None

    allowed = {
        "mg": (lambda c: c == "global" or c.startswith(("mg-", "sub-")), "global/, mg-*/ and sub-*/"),
        # Any other folder of a subscription is a region, checked as one.
        "sub": (lambda c: True, "global/ and the regions"),
        "region": (lambda c: c.startswith("rg-"), "rg-*/"),
        "rg": (None, "the units of its resources"),
        "device": (None, "the units of the device"),
        "global": (None, "units"),
    }
    for d in sorted(set(files) | set(children)):
        kind = level(d)
        if kind is None:
            continue
        own = set(files.get(d, []))
        context = SCOPE_CONTEXT.get(kind) or ("device.hcl" if kind == "device" else None)
        if context and context not in own:
            report.add("scope-layout", d + "/", 1, f"{d}/ has no {context}")
        may_unit = kind in ("mg", "sub", "rg")
        holds = " and ".join(x for x in (context, "its own unit" if may_unit else "") if x)
        for f in sorted(own - {context} - (UNIT_FILES if may_unit else set())):
            report.add("scope-layout", f"{d}/{f}", 1,
                       f"{f} does not belong in a {kind} folder: "
                       + (f"it holds {holds} only" if holds else "it holds folders only"))
        test, what = allowed[kind]
        for c in sorted(children.get(d, set())):
            child = f"{d}/{c}"
            ok = test(c) if test else (child in dirs and level(child) is None)
            if not ok:
                report.add("scope-layout", child + "/", 1, f"{child}/ in a {kind} folder: it holds {what}")
            elif test is None and children.get(child):
                report.add("scope-layout", child + "/", 1,
                           f"{child}/ is a unit with folders inside: a unit is one resource and its children, "
                           "in its own terragrunt.hcl")


# A scope's own unit sits in the folder of the scope (code/azure.md).
SCOPE_FOLDERS = {"management-group": ["mg-*"], "subscription": ["sub-*"], "resource-group": ["rg-*"]}


def rule_folder_dictionary(repo, params, units, report):
    """A unit folder is named after its module: <module> or <module>-<purpose>."""
    folders = {**SCOPE_FOLDERS, **(params.get("folders") or {})}
    for path in units:
        module, _, source_file = module_of(repo, path)
        if not module:
            continue
        name = os.path.basename(module)
        folder = os.path.basename(os.path.dirname(path))
        if source_file != path and os.path.basename(source_file) != f"{name}.hcl":
            report.add("folder-dictionary", source_file, 1,
                       f"{source_file} runs the module {module}: it is named {name}.hcl")
        globs = folders.get(name) or folders.get(module) or []
        # A folder named in the plural holds one unit per instance, named after it.
        plural = name[:-1] + "ies" if name.endswith("y") else name + "s"
        parent = os.path.basename(os.path.dirname(os.path.dirname(path)))
        if folder == name or folder.startswith(name + "-") or parent == plural \
                or (globs and matches(folder, *globs)):
            continue
        report.add("folder-dictionary", path, 1,
                   f"{folder}/ runs the module {module}: name it {name}/ or {name}-<purpose>/")


# Every repository: a to-do comment names its issue, a relative link in Markdown leads
# somewhere. Both read every file git tracks, apart from the managed copies
# of the standards in .claude/ (governance checks the originals).
NOT_OURS = [".claude/**"]
# Written in two parts so that this file does not report itself.
TODO_WORD = "TO" + "DO"
RE_TODO = re.compile(r"\b" + TODO_WORD + r"\b(?!\(#\d+\))")
SKIP_SUFFIX = (".md", ".lock", ".lock.hcl", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".pdf",
               ".zip", ".gz", ".woff", ".woff2", ".ttf", ".otf", ".pfx", ".cer", ".crt", ".der")


def text_lines(repo, path):
    """The lines of a text file, or None for a binary or unreadable one."""
    full = os.path.join(repo.root, path)
    try:
        if os.path.getsize(full) > 1_000_000:
            return None
        with open(full, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace").splitlines()


def rule_todo_issue(repo, params, units, report):
    """A to-do comment names its issue, as in (#42) after the word, so that it is found and done."""
    files = repo.glob(*params["files"]) if params.get("files") else repo.files
    skip = NOT_OURS + list(params.get("exclude") or [])
    for path in files:
        if matches(path, *skip) or path.lower().endswith(SKIP_SUFFIX):
            continue
        for n, line in enumerate(text_lines(repo, path) or [], 1):
            if RE_TODO.search(line):
                report.add("todo-issue", path, n,
                           f"{TODO_WORD} without its issue: write {TODO_WORD}(#<issue>): <what>, or open the issue")


RE_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
RE_INLINE = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*(<[^>]*>|[^\s)]+(?:\([^\s)]*\))?)(?:\s+[\"'(][^)]*)?\s*\)")
RE_REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
RE_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
RE_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
RE_HTML_ID = re.compile(r"<a\s+(?:[^>]*\s)?(?:id|name)=[\"']([^\"']+)[\"']", re.I)


def markdown_code_free(lines, blank=True):
    """(line number, text) of the lines outside fenced code, inline code blanked out unless not blank."""
    fence = None
    comment = False
    for n, line in enumerate(lines, 1):
        m = RE_FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        if comment or "<!--" in line:
            start = 0 if comment else line.index("<!--")
            end = line.find("-->", start)
            comment = end < 0
            line = line[:start] + ("" if comment else line[end + 3:])
        yield n, re.sub(r"(`+)(.+?)\1", lambda c: " " * len(c.group(0)), line) if blank else line


def slug(text: str) -> str:
    """GitHub's anchor of a heading."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").lower().strip()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(lines) -> set:
    found, seen = set(), {}
    # Inline code stays: GitHub keeps its text in the anchor (## `_common/` -> #_common).
    for _, line in markdown_code_free(lines, blank=False):
        found.update(RE_HTML_ID.findall(line))
        m = RE_HEADING.match(line)
        if not m:
            continue
        base = slug(m.group(1))
        k = seen.get(base, 0)
        seen[base] = k + 1
        found.add(base if k == 0 else f"{base}-{k}")
    return found


def rule_markdown_links(repo, params, units, report):
    """A relative link in Markdown leads to a file of the repository (and a heading in it)."""
    skip = NOT_OURS + list(params.get("exclude") or [])
    dirs = {os.path.dirname(f) for f in repo.files}
    for d in list(dirs):
        while d:
            d = os.path.dirname(d)
            dirs.add(d)
    cache = {}

    def lines_of(path):
        if path not in cache:
            cache[path] = text_lines(repo, path) or []
        return cache[path]

    for path in repo.files:
        if not path.lower().endswith(".md") or matches(path, *skip):
            continue
        for n, line in markdown_code_free(lines_of(path)):
            targets = [m.group(3) for m in RE_INLINE.finditer(line)]
            if (m := RE_REFDEF.match(line)):
                targets.append(m.group(1))
            for raw in targets:
                target = raw[1:-1] if raw.startswith("<") else raw
                if not target or RE_SCHEME.match(target) or target.startswith("//"):
                    continue
                file_part, _, anchor = target.partition("#")
                file_part = urllib.parse.unquote(file_part.split("?")[0])
                if not file_part:
                    dest = path
                elif file_part.startswith("/"):
                    dest = os.path.normpath(file_part.lstrip("/"))
                else:
                    dest = os.path.normpath(os.path.join(os.path.dirname(path), file_part))
                if dest.startswith(".."):
                    report.add("markdown-links", path, n, f"{target} leads out of the repository")
                    continue
                dest = "" if dest == "." else dest
                if dest not in repo.files and dest.rstrip("/") not in dirs:
                    report.add("markdown-links", path, n, f"{target}: {dest or '/'} does not exist")
                    continue
                if anchor and dest.lower().endswith(".md") and dest in repo.files \
                        and urllib.parse.unquote(anchor).lower() not in {a.lower() for a in anchors(lines_of(dest))}:
                    report.add("markdown-links", path, n, f"{target}: no heading #{anchor} in {dest}")


def nearest(repo, directory, name):
    while True:
        candidate = os.path.join(directory, name) if directory else name
        if repo.exists(candidate):
            return candidate
        if not directory:
            return None
        directory = os.path.dirname(directory)


RULES = {
    "variable-no-default": rule_variable_no_default,
    "variable-documented": rule_variable_documented,
    "module-source": rule_module_source,
    "dependencies-in-common": rule_dependencies_in_common,
    "mock-outputs": rule_mock_outputs,
    "tags-required": rule_tags_required,
    "no-hardcoded-ids": rule_no_hardcoded_ids,
    "no-hardcoded-location": rule_no_hardcoded_location,
    "no-envcommon": rule_no_envcommon,
    "lock-none-needs-reason": rule_lock_none_needs_reason,
    "unit-assets": rule_unit_assets,
    "scope-layout": rule_scope_layout,
    "folder-dictionary": rule_folder_dictionary,
    "todo-issue": rule_todo_issue,
    "markdown-links": rule_markdown_links,
}
# Rules every repository has without naming them; level off turns one off.
DEFAULT_ON = ("todo-issue", "markdown-links")
LEVELS = ("error", "warn", "off")


# --------------------------------------------------------------------------

def check(config: dict, root: str, only=None, skip=()):
    """(violations with their level and exception, config errors)."""
    errors = []
    rules = dict(config.get("rules") or {})
    for name in DEFAULT_ON:
        rules.setdefault(name, {"level": "error"})
    for name, params in rules.items():
        if name not in RULES:
            errors.append(f"unknown rule {name!r}; known: {', '.join(sorted(RULES))}")
        elif (params or {}).get("level", "error") not in LEVELS:
            errors.append(f"rule {name}: level must be one of {', '.join(LEVELS)}")
    exceptions = config.get("exceptions") or []
    for k, e in enumerate(exceptions):
        if not str(e.get("reason", "")).strip():
            errors.append(f"exception {k + 1} ({e.get('rule')}, {e.get('path')}) has no reason")
        if e.get("rule") not in RULES:
            errors.append(f"exception {k + 1} names an unknown rule {e.get('rule')!r}")
    if errors:
        return [], errors

    repo = Repo(root)
    units = repo.glob(*(config.get("units") or []))
    report = Report()
    for name, params in rules.items():
        params = params or {}
        if params.get("level", "error") == "off" or name in skip or (only and name not in only):
            continue
        try:
            RULES[name](repo, params, units, report)
        except (OSError, ValueError) as e:
            errors.append(f"rule {name} could not run: {e}")
    lines = {}
    for v in report.items:
        v["level"] = (rules[v["rule"]] or {}).get("level", "error")
        v["exception"] = None
        for e in exceptions:
            if e["rule"] != v["rule"] or not matches(v["path"], e.get("path", "**")):
                continue
            if e.get("match"):
                if v["path"] not in lines:
                    with open(os.path.join(root, v["path"]), encoding="utf-8") as fh:
                        lines[v["path"]] = fh.read().splitlines()
                text = lines[v["path"]][v["line"] - 1] if 0 < v["line"] <= len(lines[v["path"]]) else ""
                if e["match"] not in text and e["match"] not in v["message"]:
                    continue
            v["exception"] = e["reason"]
            break
    return report.items, errors


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(config_path: str, root: str = ".", only=None, skip=()) -> int:
    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh) or {}
    violations, errors = check(config, root, only, skip)
    for e in errors:
        print(f"::error title=conventions::{escape(e)}")
    failing = 0
    for v in violations:
        if v["exception"]:
            continue
        kind = "error" if v["level"] == "error" else "warning"
        failing += kind == "error"
        print(f"::{kind} file={v['path']},line={v['line']},title={v['rule']}::{escape(v['message'])}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    out = ["## Conventions", ""]
    active = [v for v in violations if not v["exception"]]
    accepted = [v for v in violations if v["exception"]]
    if not violations and not errors:
        out.append("Every rule holds.")
    for title, rows, extra in (("Violations", active, "level"), ("Accepted", accepted, "exception")):
        if not rows:
            continue
        out += ["", f"### {title}", "", f"| Rule | Where | What | {extra.capitalize()} |", "|---|---|---|---|"]
        out += [f"| {v['rule']} | `{v['path']}:{v['line']}` | {v['message'].replace('|', '/')} "
                f"| {str(v[extra]).replace('|', '/')} |" for v in rows]
    if errors:
        out += ["", "### Config errors", ""] + [f"- {e}" for e in errors]
    text = "\n".join(out) + "\n"
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 1 if failing or errors else 0


def cli(argv: list) -> int:
    opts, args = {}, []
    it = iter(argv)
    for a in it:
        if a in ("--only", "--skip"):
            opts[a[2:]] = [x for x in next(it, "").split(",") if x]
        else:
            args.append(a)
    if not args:
        sys.exit(__doc__)
    return main(*args[:2], only=opts.get("only"), skip=tuple(opts.get("skip") or ()))


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
