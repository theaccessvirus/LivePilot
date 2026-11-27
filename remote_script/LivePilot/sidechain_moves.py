"""Remember individual receiving devices across a source-track cut/paste in Live."""
from collections import OrderedDict
from uuid import uuid4

from .mixing import _find_sidechain_surface
from .router import register
from .transport import _get_session_instance_id


# Keep Live device references: track indices, rack paths and device names can all
# change or repeat. A repair must never substitute a different receiving device.
_snapshots = OrderedDict()


def _walk(owner, path):
    for index, device in enumerate(owner.devices):
        address = "%s / %d %s" % (path, index + 1, device.name)
        yield device, address
        if not device.can_have_chains:
            continue
        for kind, chains in (("chain", device.chains), ("return chain", getattr(device, "return_chains", ()))):
            for chain_index, chain in enumerate(chains):
                yield from _walk(chain, "%s / %s %d %s" % (address, kind, chain_index + 1, chain.name))


def _source(song, name):
    tracks = list(song.tracks)
    owners = tracks + list(song.return_tracks) + [song.master_track]
    matches = [i for i, track in enumerate(owners) if track.name == name]
    if len(matches) != 1 or matches[0] >= len(tracks):
        raise ValueError("Expected one regular source track named %r" % name)
    index = matches[0]
    labels = {name, "%d-%s" % (index + 1, name)}
    other_labels = {"", "No Input", "None", "Ext. In", "Resampling"}
    for i, owner in enumerate(owners):
        if i != index:
            other_labels.update((owner.name, "%d-%s" % (i + 1, owner.name)))
    if labels & other_labels:
        raise ValueError("Ambiguous sidechain routing label for %r" % name)
    return owners, labels


def _name(routing):
    return routing.display_name if routing is not None else ""


def _enabled(device):
    if hasattr(device, "sidechain_enabled"):
        return bool(device.sidechain_enabled)
    for parameter in device.parameters:
        if parameter.name == "S/C On":
            return bool(parameter.value)
    return None


def _one(options, names):
    matches = [option for option in options if _name(option) in names]
    if len(matches) != 1:
        raise ValueError("Expected one routing option for %s" % sorted(names))
    return matches[0]


@register("get_track_sidechain_snapshot")
def get_track_sidechain_snapshot(song, params):
    source = params["source"]
    owners, labels = _source(song, source)
    entries = []
    uninspected = []
    for owner in owners:
        for device, path in _walk(owner, owner.name):
            surface = _find_sidechain_surface(device)
            if surface is None:
                uninspected.append(path)
                continue
            selected = _name(surface["read_type"]())
            if selected not in labels:
                continue
            _one(surface["types"], labels)
            entry = dict(device=path, source=selected, tap=_name(surface["read_chan"]()),
                         enabled=_enabled(device))
            entries.append((device, entry))
    token = uuid4().hex
    _snapshots[token] = (_get_session_instance_id(song), source, entries)
    if len(_snapshots) > 32:
        _snapshots.popitem(last=False)
    return dict(snapshot=token, source=source, connections=[entry for _, entry in entries],
                without_routing_surface=uninspected)


def _restore(device, entry, labels):
    surface = _find_sidechain_surface(device)
    target = _one(surface["types"], labels)
    selected = _name(surface["read_type"]())
    changed = False
    if selected != _name(target):
        if selected not in ("", "No Input", "None", entry["source"]):
            raise ValueError("Source was edited to %r" % selected)
        surface["set_type"](target)
        changed = True
    if _name(surface["read_chan"]()) != entry["tap"]:
        channel = _one(surface["get_channels"]() or (), {entry["tap"]})
        surface["set_chan"](channel)
        changed = True
    if _enabled(device) != entry["enabled"]:
        if hasattr(device, "sidechain_enabled"):
            device.sidechain_enabled = entry["enabled"]
        else:
            next(p for p in device.parameters if p.name == "S/C On").value = int(entry["enabled"])
        changed = True
    if (_name(surface["read_type"]()), _name(surface["read_chan"]()), _enabled(device)) != (
            _name(target), entry["tap"], entry["enabled"]):
        raise ValueError("Live did not confirm the captured routing")
    return "restored" if changed else "verified"


@register("restore_track_sidechains")
def restore_track_sidechains(song, params):
    saved = _snapshots.get(params["snapshot"])
    if saved is None or saved[0] != _get_session_instance_id(song):
        raise ValueError("Unknown snapshot or different open Live Set")
    _, source, entries = saved
    owners, labels = _source(song, source)
    devices = [device for owner in owners for device, _ in _walk(owner, owner.name)]
    results = []
    for device, entry in entries:
        try:
            if device not in devices:
                raise ValueError("Original receiver is no longer in this set")
            status = _restore(device, entry, labels)
            results.append(dict(entry, status=status))
        except Exception as error:
            results.append(dict(entry, status="failed", error=str(error)))
    return dict(ok=all(item["status"] != "failed" for item in results), connections=results)
