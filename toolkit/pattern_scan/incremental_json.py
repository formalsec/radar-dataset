"""
incremental_json.py

Writes one JSON array to disk incrementally -- one record appended (and
flushed) at a time -- so peak memory during a long scan stays O(1
record) instead of O(all repos). The file is kept open for the whole
run rather than reopened per record.

If the process is killed mid-run, the file will be missing its closing
"]" and, worse, may be truncated in the MIDDLE of a record (not just
between records) -- a raw text search for e.g. the last "}" or for
'"repo_name": "..."' can therefore match inside an incomplete record
and wrongly treat it as done. `_scan_top_level_records` avoids this by
actually decoding one JSON object at a time from the start of the
array with json.JSONDecoder.raw_decode, so only FULLY complete
top-level records ever count -- any partial tail is correctly dropped.
"""

import json


class IncrementalJSONArrayWriter:
    def __init__(self, path, mode="w"):
        self.path = path
        self._file = open(path, mode, encoding="utf-8")
        self._wrote_first = False
        self._file.write("[\n")
        self._file.flush()

    def write(self, record):
        if self._wrote_first:
            self._file.write(",\n")
        self._file.write(json.dumps(record, indent=2, default=str))
        self._file.flush()
        self._wrote_first = True

    def close(self):
        self._file.write("\n]\n")
        self._file.flush()
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Always close the array bracket, even on error/interrupt, so the
        # file is valid JSON rather than a raw dangling "[".
        self.close()

    @classmethod
    def resume(cls, path):
        """
        Reopen an existing (possibly truncated -- crash, Ctrl+C, disk
        full mid-write, etc.) output file and keep appending to it,
        WITHOUT loading previous records into memory. Truncates the
        file back to right after the last FULLY COMPLETE top-level
        record (via _scan_top_level_records, not a naive brace search)
        and continues writing from there, so a record cut off halfway
        through is dropped rather than silently accepted as done.
        """
        text = open(path, encoding="utf-8").read()
        _names, end_offset = _scan_top_level_records(text)
        if end_offset == -1:
            return cls(path, mode="w")

        obj = cls.__new__(cls)
        obj.path = path
        obj._file = open(path, "r+", encoding="utf-8")
        obj._file.seek(end_offset)
        obj._file.truncate()
        obj._file.flush()
        obj._wrote_first = True
        return obj


def _scan_top_level_records(text, key="repo_name"):
    """
    Decode complete top-level JSON objects one at a time from an
    (opening-bracket-started, possibly truncated) array, using
    json.JSONDecoder.raw_decode so nested braces inside a record never
    get mistaken for the record's own closing brace. Stops at the first
    object it can't fully decode (partial/truncated tail) and reports
    where the text of the last good one ended.

    Returns (list_of_values_for_key, char_offset_right_after_last_complete_record).
    offset is -1 if there isn't even one complete record yet.
    """
    start = text.find("[")
    if start == -1:
        return [], -1
    idx = start + 1
    n = len(text)
    decoder = json.JSONDecoder()
    values = []
    last_good_end = -1

    while True:
        while idx < n and text[idx] in " \t\r\n,":
            idx += 1
        if idx >= n or text[idx] == "]":
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break  # partial/truncated trailing record -- stop here
        if isinstance(obj, dict):
            values.append(obj.get(key))
        last_good_end = end
        idx = end

    return values, last_good_end


def already_processed_keys(path, key="repo_name"):
    """
    Which records already fully exist in a (possibly incomplete, e.g.
    mid-crash) output file. Uses the same complete-record-only scanner
    as resume(), so a record truncated mid-write is correctly treated
    as NOT done rather than matched by a stray key fragment.
    Returns an empty set if the file doesn't exist yet.
    """
    try:
        text = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return set()
    values, _ = _scan_top_level_records(text, key=key)
    return {v for v in values if v is not None}


def repair_and_load(path):
    """
    Best-effort load of a possibly-truncated incremental JSON array
    file: returns every fully complete top-level record as a list
    (dropping a partial trailing record if the file was cut off
    mid-write), or [] if nothing usable is found.
    """
    text = open(path, encoding="utf-8").read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    if start == -1:
        return []
    idx = start + 1
    n = len(text)
    decoder = json.JSONDecoder()
    records = []
    while True:
        while idx < n and text[idx] in " \t\r\n,":
            idx += 1
        if idx >= n or text[idx] == "]":
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        records.append(obj)
        idx = end
    return records
