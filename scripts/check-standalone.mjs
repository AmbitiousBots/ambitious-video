#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const USAGE = `Usage: node scripts/check-standalone.mjs [options]

Checks that a repository reads as a standalone public project: no internal
plan IDs and no machine-specific paths in anything an outside contributor sees.

By default every git-tracked text file is scanned.

Options:
  --repo <dir>        Repository to scan (default: current directory).
  --changed <base>    Scan only files changed between <base> and HEAD.
  --text-file <file>  Also scan this file, for example a saved pull request
                      title and description. May be repeated.
  --commits <range>   Also scan the commit messages in this git range,
                      for example origin/main..HEAD.
  --deny-file <file>  Extra patterns to refuse, one regular expression per
                      line; blank lines and lines starting with # are skipped.
                      Keep organisation-specific names in such a file outside
                      the repository. May be repeated.
  --help              Print this text.

Documented runtime paths are allowed by listing them in .standalone.json at the
repository root:
  { "allow": [{ "pattern": "<path prefix>", "reason": "<why it is public>" }] }
Every entry needs a reason, and on a full scan every entry must be used.

Exit code 0: nothing found. 1: at least one finding. 2: the check could not run.
`;

const CONFIG_FILE = ".standalone.json";
const MAX_FILE_BYTES = 2_000_000;
const PATH_START = String.raw`(?<![\w.~$}-])`;
const PATH_REST = "[^\\s`'\"()\\[\\]<>,;]*";
const USER_NAME = "[A-Za-z0-9_.-]+";
const MACHINE_ROOTS = [
  ["", "workspace", ""].join("/"),
  ["", "Users", ""].join("/"),
  ["", "home", USER_NAME, ""].join("/"),
  ["[A-Za-z]:", "Users", ""].join(String.raw`\\`),
];

const BUILT_IN_RULES = [
  {
    id: "plan-id",
    pattern: /\bP\d{1,2}-\d{2}\b/g,
    fix: "Replace the internal plan ID with a plain description of the requirement.",
    allowable: false,
  },
  {
    id: "machine-path",
    pattern: new RegExp(`${PATH_START}(?:${MACHINE_ROOTS.join("|")})${PATH_REST}`, "g"),
    fix: `Replace the machine path with a repo-relative path or a documented variable such as $AMBITIOUS_HOME, or list a documented runtime path in ${CONFIG_FILE} with a reason.`,
    allowable: true,
  },
];

class CannotRun extends Error {}

const parseArgs = (argv) => {
  const options = { repo: ".", changed: null, textFiles: [], commits: null, denyFiles: [], help: false };
  const takesValue = {
    "--repo": (value) => ({ repo: value }),
    "--changed": (value) => ({ changed: value }),
    "--commits": (value) => ({ commits: value }),
    "--text-file": (value) => ({ textFiles: [...options.textFiles, value] }),
    "--deny-file": (value) => ({ denyFiles: [...options.denyFiles, value] }),
  };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--help") {
      options.help = true;
      continue;
    }
    const assign = takesValue[flag];
    if (assign === undefined) throw new CannotRun(`Unknown option "${flag}".`);
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) throw new CannotRun(`Option ${flag} needs a value.`);
    Object.assign(options, assign(value));
    index += 1;
  }
  return options;
};

const readTextFile = (path, what) => {
  try {
    return readFileSync(path, "utf8");
  } catch (error) {
    throw new CannotRun(`Cannot read ${what} "${path}": ${error.message}`);
  }
};

const git = (repo, args) => {
  try {
    return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
  } catch (error) {
    throw new CannotRun(`git ${args.join(" ")} failed: ${String(error.stderr || error.message).trim()}`);
  }
};

const denyRules = (path) =>
  readTextFile(path, "deny file")
    .split("\n")
    .map((line, index) => ({ text: line.trim(), line: index + 1 }))
    .filter(({ text }) => text !== "" && !text.startsWith("#"))
    .map(({ text, line }) => {
      try {
        return {
          id: "denied-term",
          pattern: new RegExp(text, "g"),
          fix: "Remove the organisation-internal name or reference and describe the behaviour in public terms.",
          allowable: false,
        };
      } catch (error) {
        throw new CannotRun(`${path}:${line}: not a valid regular expression: ${error.message}`);
      }
    });

const isNonEmptyString = (value) => typeof value === "string" && value.trim() !== "";

const loadAllowList = (repo) => {
  const path = join(repo, CONFIG_FILE);
  if (!existsSync(path)) return { entries: [], findings: [] };
  const finding = (message) => ({
    source: CONFIG_FILE,
    line: 1,
    rule: "allow-list",
    match: null,
    fix: message,
  });
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    return { entries: [], findings: [finding(`Make ${CONFIG_FILE} valid JSON: ${error.message}`)] };
  }
  const allow = parsed?.allow;
  if (!Array.isArray(allow)) {
    return {
      entries: [],
      findings: [finding(`Give ${CONFIG_FILE} the shape { "allow": [{ "pattern": "...", "reason": "..." }] }.`)],
    };
  }
  const findings = allow.flatMap((entry, index) => {
    if (!isNonEmptyString(entry?.pattern)) return [finding(`Give allow entry ${index + 1} a non-empty "pattern".`)];
    if (!isNonEmptyString(entry?.reason)) {
      return [finding(`Give allow entry ${index + 1} ("${entry.pattern}") a "reason" that says why the path is public.`)];
    }
    return [];
  });
  const entries = allow
    .filter((entry) => isNonEmptyString(entry?.pattern) && isNonEmptyString(entry?.reason))
    .map((entry) => ({ pattern: entry.pattern, used: false }));
  return { entries, findings };
};

const coveredBy = (pattern, match) => {
  const prefix = pattern.endsWith("/") ? pattern.slice(0, -1) : pattern;
  if (!match.startsWith(prefix)) return false;
  const next = match.charAt(prefix.length);
  return next === "" || !/[A-Za-z0-9_-]/.test(next);
};

const trackedSources = (repo, changedBase) => {
  const names = git(
    repo,
    changedBase === null
      ? ["ls-files", "-z"]
      : ["diff", "--name-only", "--diff-filter=ACMR", "-z", `${changedBase}...HEAD`],
  )
    .split("\0")
    .filter((name) => name !== "" && name !== CONFIG_FILE);
  return names.flatMap((name) => {
    const path = join(repo, name);
    if (!existsSync(path)) return [];
    const bytes = readFileSync(path);
    if (bytes.includes(0) || bytes.length > MAX_FILE_BYTES) return [];
    return [{ name, text: bytes.toString("utf8") }];
  });
};

const commitSources = (repo, range) =>
  git(repo, ["log", "--format=%H%n%B%x00", range])
    .split("\0")
    .map((record) => record.replace(/^\n+/, ""))
    .filter((record) => record !== "")
    .map((record) => {
      const [hash, ...message] = record.split("\n");
      return { name: `commit ${hash.slice(0, 12)}`, text: message.join("\n") };
    });

const scan = (sources, rules, allowEntries) =>
  sources.flatMap((source) =>
    source.text.split("\n").flatMap((lineText, index) =>
      rules.flatMap((rule) =>
        [...lineText.matchAll(rule.pattern)].flatMap(([raw]) => {
          const match = rule.allowable ? raw.replace(/[.:!?*]+$/, "") : raw;
          const allowedBy = rule.allowable ? allowEntries.find((entry) => coveredBy(entry.pattern, match)) : undefined;
          if (allowedBy !== undefined) {
            allowedBy.used = true;
            return [];
          }
          return [{ source: source.name, line: index + 1, rule: rule.id, match, fix: rule.fix }];
        }),
      ),
    ),
  );

const run = (argv) => {
  const options = parseArgs(argv);
  if (options.help) {
    process.stdout.write(USAGE);
    return 0;
  }
  const rules = [...BUILT_IN_RULES, ...options.denyFiles.flatMap(denyRules)];
  const allowList = loadAllowList(options.repo);
  const sources = [
    ...trackedSources(options.repo, options.changed),
    ...options.textFiles.map((path) => ({ name: path, text: readTextFile(path, "text file") })),
    ...(options.commits === null ? [] : commitSources(options.repo, options.commits)),
  ];
  const hits = scan(sources, rules, allowList.entries);
  const unused =
    options.changed === null
      ? allowList.entries
          .filter((entry) => !entry.used)
          .map((entry) => ({
            source: CONFIG_FILE,
            line: 1,
            rule: "allow-list",
            match: entry.pattern,
            fix: "Remove this allow entry: nothing in the repository uses it any more.",
          }))
      : [];
  const findings = [...allowList.findings, ...hits, ...unused];
  if (findings.length === 0) {
    process.stdout.write(`standalone check: ok (${sources.length} sources scanned)\n`);
    return 0;
  }
  process.stdout.write(
    `standalone check: ${findings.length} finding(s). This repository is public and must make sense without access to any organisation's internals.\n`,
  );
  for (const finding of findings) {
    const quoted = finding.match === null ? "" : ` "${finding.match}"`;
    process.stdout.write(`  ${finding.source}:${finding.line}: ${finding.rule}${quoted}\n    ${finding.fix}\n`);
  }
  return 1;
};

try {
  process.exitCode = run(process.argv.slice(2));
} catch (error) {
  const detail = error instanceof CannotRun ? `${error.message}\nRun with --help for usage.` : error.stack;
  process.stderr.write(`standalone check: ${detail}\n`);
  process.exitCode = 2;
}
