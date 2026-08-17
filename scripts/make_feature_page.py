"""Builds the feature catalogue: what the big security systems do, and what VMD does.

    uv run python scripts/make_feature_page.py

Writes `docs/features/security-features.html`, a single self-contained page that
opens on any machine including the offline one, and a body-only copy beside it
for publishing.

Why this exists
---------------

"Search for the best security softwares that are available and try copy their
features into our software. Make a HTML including all features and I will decide
which we are going to add."

So this is a catalogue to decide from, not a plan. Every row says the same five
things - what the feature is, who has it, whether VMD has it, what it would be
worth HERE, and what it would cost - because a feature list without the third
and fourth columns is a wish list, and this system is two cameras on an
air-gapped desktop watching one perimeter, not a hundred cameras in a shopping
centre. A good half of what the big platforms sell is either impossible here
(anything that phones home) or meaningless here (multi-site, federation, roles
on a machine one person stands at).

The page is built from `FEATURES` below rather than written by hand, so the
counts at the top cannot disagree with the rows underneath.

The look is the product's own - `vmd/desktop/style.py`'s palette and type scale,
including its rule that no state is ever carried by colour alone, which is why
every status chip has a glyph as well as a colour.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

# Status of each feature in VMD today.
#
#   have       it is in the product now
#   partial    something of it is there, and the row says what is missing
#   missing    not there, and could be
#   never      not there and should not be: it cannot work on an air-gapped
#              machine, or it is meaningless for two cameras and one operator.
#              Listed anyway, because "why haven't we got X" deserves an answer
#              rather than an absence.
STATUS = {
    "have": ("In VMD", "●"),
    "partial": ("Partly", "◐"),
    "missing": ("Not yet", "○"),
    "never": ("Not for us", "✕"),
}

# Rough cost, in the only units that mean anything before the work starts.
EFFORT = {
    "S": "a day or so",
    "M": "a few days",
    "L": "a week or more",
    "HW": "needs hardware",
    "-": "",
}

# (name, what it is, who has it, status, effort, what it is worth here, pick)
#
# `pick` marks the ones worth doing first for THIS deployment. It is an opinion
# and it is labelled as one on the page.
FEATURES: list[tuple[str, list]] = [
    ("Finding things that matter", [
        ("Areas to ignore",
         "Paint out the parts of the picture that are never news - a road, a treeline, a flag.",
         "Everyone. Frigate, Milestone, Blue Iris, SightLogix.",
         "have", "-",
         "Freehand, drawn on a still of what the camera sees, with no numbers anywhere. Rescales itself if the stream resolution changes.",
         False),
        ("Areas to watch",
         "The opposite: alarm ONLY inside a drawn area, and stay silent everywhere else.",
         "Frigate (zones), Axis Perimeter Defender, Genetec, Milestone.",
         "missing", "M",
         "The single biggest false-alarm cut available. Right now everything outside the ignore areas is watched, so an area of interest has to be built by painting out the whole rest of the world.",
         True),
        ("Tripwire, with a direction",
         "A line across the picture. Alarm when something crosses it, and only one way if you say so.",
         "Axis, Genetec, Milestone, Hanwha, most cameras themselves.",
         "missing", "M",
         "This is what a perimeter IS. \"Someone crossed the fence line inward\" is a different fact from \"something moved\", and it is the one worth waking up for.",
         True),
        ("Must stay in the area for N seconds",
         "Ignore anything that only clips the edge of a zone in passing.",
         "Frigate calls it inertia; Axis and Genetec call it dwell.",
         "missing", "S",
         "Cheap on top of areas to watch, and kills the branch that swings across a corner of the frame.",
         False),
        ("Loitering",
         "Alarm when something stays put for longer than it should.",
         "Axis, Genetec, SightLogix, Hanwha.",
         "missing", "M",
         "Somebody standing still at 700 m is exactly what movement detection is worst at: they stop moving and the track dies.",
         True),
        ("Speed and direction filters",
         "Too fast is a vehicle, too slow is drift, and the wrong bearing is not your problem.",
         "SightLogix, Axis, Genetec.",
         "partial", "M",
         "There is a travel rule (the wind rule) but no speed and no bearing. Bearing would let the far side of the road be ignored without painting it out.",
         False),
        ("Size in metres, not pixels",
         "Calibrate the view once, then say \"ignore anything smaller than a person at that range\".",
         "SightLogix builds their whole product on this (georegistration).",
         "missing", "L",
         "The size rule is in pixels and a share of frame height today, which means it changes meaning every time the camera zooms. A rabbit at 40 m and a man at 700 m are the same number of pixels.",
         True),
        ("Human-shaped filter",
         "A person is taller than wide. Reject blobs that are not.",
         "SightLogix (aspect ratio filtering).",
         "missing", "S",
         "Very cheap, and it throws away a lot of what a treeline produces.",
         False),
        ("Steady the picture before looking at it",
         "Take the mast sway out of the frame so wind does not read as movement.",
         "SightLogix stabilise in the camera before analytics run.",
         "missing", "L",
         "A pole at 700 m moves in wind, and every pixel moves with it. This is the difference between usable and unusable on a windy night.",
         False),
        ("Say what it was - person, vehicle, animal",
         "A model puts a noun on what moved.",
         "Frigate, Axis, SightLogix, Genetec, Milestone.",
         "never", "-",
         "You asked for this removed and the arithmetic agrees: at 700 m a person is about 13 pixels, and a model trained on photographs has nothing to say about that. The code is still there behind one switch if you change your mind.",
         False),
        ("Two levels: worth waking for, and worth logging",
         "Some movement is an alarm, the rest is just a record.",
         "Frigate (alerts vs detections).",
         "missing", "S",
         "Everything is equal now. A tripwire crossing and a bird are the same event with the same sound.",
         True),
        ("Different rules at different times of day",
         "Arm at dusk, relax during working hours, tighten at night.",
         "Blue Iris (profiles and schedules), Milestone, Genetec.",
         "missing", "M",
         "The perimeter is not the same problem at 14:00 and 03:00, and today one setting has to serve both.",
         False),
        ("Sound detection",
         "Alarm on a gunshot, glass, a shout.",
         "Frigate (600+ sounds), Genetec.",
         "never", "-",
         "There is no audio anywhere in this system on purpose: the camera sends a format MP4 cannot hold, and it cost a day of recording once already.",
         False),
    ]),

    ("Knowing the camera is still telling the truth", [
        ("Tamper alarm - covered, turned, sprayed, defocused",
         "Alarm when the VIEW changes, not when something moves in it.",
         "Genetec, Milestone, Axis, Hanwha.",
         "partial", "M",
         "A flat or frozen picture is already noticed and reported as blindness, and a camera that has been moved suppresses its own frame. Neither of them raises anything the operator hears. This is the attack a perimeter system has to survive.",
         True),
        ("Was it recording all night?",
         "A report of every gap in the recording, with the reason.",
         "Milestone, Genetec, Nx Witness.",
         "missing", "S",
         "The segment index already knows. Nothing asks it. \"Yes, all of it\" is the question that gets asked the morning after something happened, and today the only answer is scrolling the Logs tab.",
         True),
        ("Show the rules that are throwing things away",
         "How many blobs each rejection rule discarded: too small, above the horizon, inside an ignore area.",
         "Nobody shows this well. Frigate's debug view comes closest.",
         "partial", "S",
         "These counters are already computed and published, and nothing displays them. A size rule set wrong deletes every real detection and says nothing - and on FHD the standard sensitivity may be doing exactly that.",
         True),
        ("Health at a glance",
         "One page: every camera, recording, disk, link, up or down.",
         "Milestone, Genetec, Nx Witness all sell this hard.",
         "have", "-",
         "The status band across the top, plus the Logs tab. Smaller than theirs and it fits two cameras.",
         False),
        ("Disk failure warning, not just disk full",
         "SMART attributes, write errors, a drive on its way out.",
         "Milestone, Genetec.",
         "partial", "M",
         "Free space and write failures are watched. The drive's own opinion of itself is not asked for.",
         False),
        ("Clock sanity",
         "Warn when the machine's time is wrong, because every recording is filed under it.",
         "Milestone, Genetec (NTP monitoring).",
         "missing", "S",
         "This machine has no internet and its clock is set by hand. The code already survives the clock going backwards; nothing warns anybody that it did.",
         False),
        ("Redundant recording / failover server",
         "A second machine takes over when the first dies.",
         "Milestone, Genetec, Nx Witness.",
         "never", "-",
         "One box, air-gapped. A second one is a procurement decision and not a feature.",
         False),
    ]),

    ("What the operator does when something happens", [
        ("A sound",
         "Make a noise in the room.",
         "Everyone.",
         "have", "-",
         "Two alternating tones, purpose-built so it is not mistaken for a Windows notification, and it can be switched off.",
         False),
        ("The picture of what moved, with the box on it",
         "A still of the exact frame the detection was confirmed on.",
         "Frigate (snapshots), Milestone, Genetec.",
         "have", "-",
         "In the side column, needing no dismissing, and saved to disk so it can be sent to somebody.",
         False),
        ("An alarm list with a state on each one",
         "New, acknowledged, closed - with who and when.",
         "Every serious VMS.",
         "missing", "M",
         "You had the red strip removed and were right to. What a list gives that the strip did not is history: the ten things that happened while nobody was in the room.",
         False),
        ("Silence one camera for an hour",
         "Time-limited, and it un-silences itself.",
         "Genetec (shelving), most alarm systems.",
         "missing", "S",
         "The honest answer to a windy night. Today the only lever is switching detection off, which stays off.",
         True),
        ("Escalate if nobody responds",
         "Nobody acknowledged in N minutes - do something louder.",
         "Genetec Mission Control, most alarm platforms.",
         "missing", "M",
         "Only worth it with somewhere to escalate TO. On an air-gapped box that means a louder sound or a relay.",
         False),
        ("A checklist on the alarm",
         "What to do about this, written down, at the moment it happens.",
         "Genetec (action plans), Milestone.",
         "missing", "M",
         "The user guide covers this. On screen at 03:40 is better than in a PDF.",
         False),
        ("Mark a moment by hand",
         "Bookmark something you noticed, so you can find it later.",
         "Milestone, Nx Witness, Genetec.",
         "missing", "S",
         "The timeline only shows what the detector found. There is no way to mark something you saw with your own eyes.",
         False),
        ("Notify a phone",
         "Push, SMS, email.",
         "Everyone.",
         "never", "-",
         "Impossible and deliberately so. This machine has no network of any kind, and that is a requirement of the site.",
         False),
    ]),

    ("Driving the camera", [
        ("Steer by keys and by dragging the picture",
         "Point the camera at something.",
         "Everyone.",
         "have", "-",
         "Arrow keys, Shift for fine, drag near an edge to slew, and a zoom bar per lens on a shared gimbal.",
         False),
        ("Saved positions",
         "Store \"the gate\", \"the north fence\", jump back to them in one click.",
         "Everyone, including the camera itself.",
         "missing", "S",
         "There is a Home and nothing else. Nine tenths of the steering an operator does is going back to the same three places.",
         True),
        ("Go back on its own",
         "After nobody has touched it for N minutes, return to the position that matters.",
         "Everyone (autoguard / return to home).",
         "missing", "S",
         "The failure this prevents: somebody looks at something at 02:00, walks away, and the perimeter is unwatched until morning with nothing on the screen saying so.",
         True),
        ("Patrol between positions",
         "Tour the saved views on a timer.",
         "Everyone.",
         "missing", "M",
         "Worth having, worth thinking about: a camera on patrol is not watching the other 90% of its cycle, and movement detection is fighting a moving background the whole time.",
         False),
        ("Follow what it found",
         "The camera tracks the moving thing by itself.",
         "Axis (licensed extra), Hanwha, Genetec.",
         "missing", "L",
         "Impressive and hard. It also takes the camera off the perimeter to chase a rabbit, and there is no second camera covering while it does.",
         False),
        ("Point at what the other camera found",
         "One sensor detects, the other slews to it and zooms in.",
         "SightLogix, Thermal Radar, Axis - the standard perimeter pattern.",
         "missing", "L",
         "You have exactly the right hardware for this: thermal finds it, visible goes and looks. It is the most valuable hard thing on this list.",
         False),
        ("Zoom into the picture without moving the camera",
         "Digital zoom on the live view.",
         "Everyone.",
         "missing", "S",
         "Optical zoom moves the lens and takes the whole view with it. Digital zoom lets one operator look closer while the recording keeps the wide view.",
         False),
        ("Brightness and contrast on the thermal",
         "Stretch the picture so a warm thing stands out from a cold field.",
         "FLIR's own tools, Hanwha, Genetec.",
         "missing", "M",
         "A thermal picture at 03:00 in winter is a grey rectangle with a slightly less grey rectangle in it.",
         False),
    ]),

    ("Recordings and evidence", [
        ("Record everything, all the time",
         "Continuous recording rather than only on movement.",
         "All of them offer it; many home systems default to event-only.",
         "have", "-",
         "Continuous, segmented, with an index and automatic deletion of the oldest. Stronger than the event-only default, because it holds the ten seconds BEFORE anybody noticed.",
         False),
        ("Protect a recording from being deleted",
         "Lock the night that matters so retention cannot reclaim it.",
         "Milestone call it Evidence Lock and sell it as a tier upgrade.",
         "missing", "M",
         "The most serious gap on this page. Retention deletes the oldest footage to stay inside the budget, and it will happily delete the incident somebody is going to ask about next week. Nothing can currently stop it.",
         True),
        ("Export a clip",
         "Cut a piece out and save it to a file.",
         "Everyone.",
         "have", "-",
         "Marked on the timeline and saved out. On the Playback tab, which is switched off by default now.",
         False),
        ("Prove the clip was not edited",
         "A signature or hash so an export can be shown to somebody who has to trust it.",
         "Milestone (signed exports), Genetec.",
         "missing", "M",
         "If footage from this system is ever going to matter to anybody outside the room, it needs to arrive with something that says it was not altered.",
         False),
        ("Search the recording for movement in one spot",
         "Draw a box on the past and find every time something moved inside it.",
         "Milestone Smart Search, Nx Witness, Genetec.",
         "missing", "L",
         "The one investigation tool that genuinely saves hours. It needs the Playback tab to be on.",
         False),
        ("Thumbnails while scrubbing",
         "See where you are on the timeline without waiting for video to load.",
         "Frigate (previews), Nx Witness, Milestone.",
         "missing", "M",
         "Playback is off by default now, so this is only worth it if it comes back into use.",
         False),
        ("Both cameras in step",
         "Play two recordings from the same moment side by side.",
         "Milestone, Genetec, Nx Witness.",
         "have", "-",
         "Both-together on the Playback tab, sharing one clock.",
         False),
        ("Save a still from the live picture",
         "One button, one JPEG of what is on screen right now.",
         "Everyone.",
         "missing", "S",
         "Stills are saved automatically when something moves. There is no way to grab one because you want one.",
         False),
        ("Who did what, and when",
         "An audit trail of operator actions, separate from the technical log.",
         "Milestone, Genetec - usually for compliance reasons.",
         "missing", "M",
         "The Logs tab records what the SYSTEM did. Nothing records that somebody turned detection off at 21:40.",
         False),
    ]),

    ("The link, the disk, the machine", [
        ("Match the picture to what the radio can carry",
         "Turn the camera's bitrate down when the link is full, and back up when it is not.",
         "Nobody does this. Milestone and Genetec monitor bandwidth; none of them drive the camera from it.",
         "have", "-",
         "This is VMD's own, and it exists because this link is the binding constraint. It reads airtime rather than megabits, because airtime is what a wireless link runs out of.",
         False),
        ("Show the radio link",
         "Signal, airtime, throughput, and per-camera on a shared access point.",
         "Nobody. A VMS shows you the camera is offline, not why.",
         "have", "-",
         "One word, two bars, and every sentence behind a toggle. It found the fault that was freezing one of your pictures.",
         False),
        ("Run with no internet at all",
         "Install, update and operate a machine that has never been on a network.",
         "Most are cloud-connected by default. Frigate and ZoneMinder can run offline; the enterprise ones assume a licence server.",
         "have", "-",
         "One folder, one zip, no runtime downloads, and three separate outbound-traffic bugs found and closed.",
         False),
        ("Trigger a light or a siren",
         "Close a relay when something crosses the line.",
         "Everyone, through the camera's own output or an I/O module.",
         "missing", "HW",
         "The most effective perimeter response there is, and it needs a contact closure - the camera's own alarm output, or a USB relay. Worth deciding before the next site visit.",
         False),
        ("Tell another system",
         "ONVIF events, or a message on a wire, so a control room knows.",
         "Everyone.",
         "never", "-",
         "There is nothing else on this network, because there is no network.",
         False),
        ("Users, passwords, roles",
         "Different people see and do different things.",
         "Everyone.",
         "never", "-",
         "One machine, in a locked room, one operator. Passwords are shown in plain text on purpose here, and that is the right call for this site rather than a shortcut.",
         False),
        ("Blackout regions in the recording",
         "Privacy masks that are burnt into what is stored.",
         "Everyone, usually for a legal reason.",
         "missing", "M",
         "Only matters if the camera can see somewhere it should not. Worth knowing whether it can.",
         False),
        ("Back up the settings",
         "One file, restorable, so a rebuild is minutes.",
         "Everyone.",
         "partial", "S",
         "It is all in settings.json and the areas you drew are in it. Nothing copies it anywhere, so a disk failure takes the setup with it.",
         True),
    ]),
]


PAGE_TITLE = "What the other systems do, and what VMD does"


def _rows() -> list[dict]:
    found = []
    for group, items in FEATURES:
        for name, what, who, status, effort, why, pick in items:
            found.append(
                {
                    "group": group,
                    "name": name,
                    "what": what,
                    "who": who,
                    "status": status,
                    "effort": effort,
                    "why": why,
                    "pick": pick,
                }
            )
    return found


def _counts(rows: list[dict]) -> dict:
    counts = {key: 0 for key in STATUS}
    for row in rows:
        counts[row["status"]] += 1
    return counts


def build_body() -> str:
    rows = _rows()
    counts = _counts(rows)
    picks = sum(1 for row in rows if row["pick"])

    parts: list[str] = []
    add = parts.append

    add('<header class="top">')
    add('<p class="eyebrow">VMD &middot; feature review</p>')
    add(f"<h1>{html.escape(PAGE_TITLE)}</h1>")
    add(
        '<p class="stand">Everything the leading systems sell, measured against what this '
        'console already does. Tick what you want; the list you build is saved in this page '
        'and can be copied out at the bottom.</p>'
    )
    add('<dl class="tally">')
    for key, (label, glyph) in STATUS.items():
        add(
            f'<div class="tally-item is-{key}"><dt><span class="glyph">{glyph}</span>{label}</dt>'
            f'<dd>{counts[key]}</dd></div>'
        )
    add(
        f'<div class="tally-item is-pick"><dt><span class="glyph">★</span>My picks</dt>'
        f'<dd>{picks}</dd></div>'
    )
    add("</dl>")
    add(
        '<p class="note">Judged for <strong>this</strong> site: two thermal and visible PTZ '
        'cameras on one radio link, an air-gapped desktop, one operator. A feature that is '
        'excellent in a shopping centre can be worthless here, and a few are impossible on '
        'a machine with no network. Those are marked <span class="inline-chip is-never">'
        '<span class="glyph">&#10005;</span> Not for us</span> and say why rather than being '
        'left out.</p>'
    )
    add("</header>")

    add('<div class="controls" role="search">')
    add(
        '<input type="search" id="find" placeholder="Filter by word - tripwire, disk, zoom..." '
        'aria-label="Filter features">'
    )
    add('<div class="filters" role="group" aria-label="Show only">')
    add('<button type="button" class="chip on" data-filter="all">Everything</button>')
    add('<button type="button" class="chip" data-filter="pick">★ My picks</button>')
    for key, (label, glyph) in STATUS.items():
        add(
            f'<button type="button" class="chip is-{key}" data-filter="{key}">'
            f'<span class="glyph">{glyph}</span>{label}</button>'
        )
    add("</div>")
    add("</div>")

    for group, items in FEATURES:
        gid = "g" + "".join(ch for ch in group.lower() if ch.isalnum())
        add(f'<section class="group" id="{gid}">')
        add(f"<h2>{html.escape(group)}</h2>")
        add('<ul class="rows">')
        for name, what, who, status, effort, why, pick in items:
            key = f"{gid}:{name}"
            label, glyph = STATUS[status]
            picked = ' data-pick="1"' if pick else ""
            add(
                f'<li class="row is-{status}" data-status="{status}"{picked} '
                f'data-text="{html.escape((name + " " + what + " " + who + " " + why).lower(), quote=True)}">'
            )
            add('<div class="tick">')
            add(
                f'<input type="checkbox" id="{html.escape(key, quote=True)}" '
                f'data-key="{html.escape(key, quote=True)}">'
                f'<label for="{html.escape(key, quote=True)}"><span class="sr">Choose {html.escape(name)}</span></label>'
            )
            add("</div>")
            add('<div class="body">')
            star = '<span class="star" title="Worth doing first">★</span>' if pick else ""
            add(f'<h3>{html.escape(name)}{star}</h3>')
            add(f'<p class="what">{html.escape(what)}</p>')
            add(f'<p class="why"><span class="lead">Here:</span> {html.escape(why)}</p>')
            add(f'<p class="who"><span class="lead">Who has it:</span> {html.escape(who)}</p>')
            add("</div>")
            add('<div class="meta">')
            add(f'<span class="chip-flat is-{status}"><span class="glyph">{glyph}</span>{label}</span>')
            if EFFORT.get(effort):
                add(f'<span class="effort">{html.escape(EFFORT[effort])}</span>')
            add("</div>")
            add("</li>")
        add("</ul>")
        add("</section>")

    add('<section class="group" id="picked">')
    add("<h2>What you have chosen</h2>")
    add('<p class="what">Ticks are kept in this page on this machine. Copy the list and send it back.</p>')
    add('<ol id="chosen" class="chosen"><li class="none">Nothing ticked yet.</li></ol>')
    add('<div class="actions">')
    add('<button type="button" id="copy" class="button">Copy the list</button>')
    add('<button type="button" id="clear" class="button quiet">Clear every tick</button>')
    add('<span id="said" class="said" role="status" aria-live="polite"></span>')
    add("</div>")
    add("</section>")

    add('<footer class="foot">')
    add(
        "<p>Sources: Milestone XProtect, Genetec Security Center, Network Optix Nx Witness, "
        "Axis Perimeter Defender, SightLogix, Frigate NVR, Blue Iris, ZoneMinder, Shinobi, "
        "Agent DVR, Thermal Radar. Read in August 2026.</p>"
    )
    add(
        "<p>Built from <code>scripts/make_feature_page.py</code>, so the counts at the top "
        "cannot disagree with the rows underneath.</p>"
    )
    add("</footer>")

    add(f"<script>{_SCRIPT}</script>")
    return "\n".join(parts)


_SCRIPT = """
(function () {
  var KEY = 'vmd.features.v1';
  var boxes = Array.prototype.slice.call(document.querySelectorAll('input[type=checkbox]'));
  var chosen = document.getElementById('chosen');
  var said = document.getElementById('said');

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  }
  function save(state) {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* private mode */ }
  }

  var state = load();
  boxes.forEach(function (box) {
    if (state[box.dataset.key]) { box.checked = true; }
    box.addEventListener('change', function () {
      state[box.dataset.key] = box.checked;
      save(state);
      draw();
    });
  });

  function picked() {
    return boxes.filter(function (b) { return b.checked; }).map(function (b) {
      var row = b.closest('.row');
      return {
        group: row.closest('.group').querySelector('h2').textContent,
        name: row.querySelector('h3').childNodes[0].textContent.trim()
      };
    });
  }

  function draw() {
    var list = picked();
    chosen.innerHTML = '';
    if (!list.length) {
      var none = document.createElement('li');
      none.className = 'none';
      none.textContent = 'Nothing ticked yet.';
      chosen.appendChild(none);
      return;
    }
    list.forEach(function (item) {
      var li = document.createElement('li');
      var strong = document.createElement('strong');
      strong.textContent = item.name;
      var small = document.createElement('span');
      small.className = 'in';
      small.textContent = item.group;
      li.appendChild(strong);
      li.appendChild(small);
      chosen.appendChild(li);
    });
  }

  document.getElementById('copy').addEventListener('click', function () {
    var list = picked();
    if (!list.length) { tell('Nothing ticked yet.'); return; }
    var text = list.map(function (i) { return '- ' + i.name + '  (' + i.group + ')'; }).join('\\n');
    var done = function () { tell('Copied ' + list.length + '. Paste it back to me.'); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallback(text, done); });
    } else { fallback(text, done); }
  });

  function fallback(text, done) {
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); done(); }
    catch (e) { tell('Could not copy. Select the list above by hand.'); }
    document.body.removeChild(area);
  }

  document.getElementById('clear').addEventListener('click', function () {
    boxes.forEach(function (b) { b.checked = false; });
    state = {};
    save(state);
    draw();
    tell('Cleared.');
  });

  var timer;
  function tell(words) {
    said.textContent = words;
    clearTimeout(timer);
    timer = setTimeout(function () { said.textContent = ''; }, 4000);
  }

  // --- filtering -----------------------------------------------------------
  var rows = Array.prototype.slice.call(document.querySelectorAll('.row'));
  var groups = Array.prototype.slice.call(document.querySelectorAll('.group'));
  var chips = Array.prototype.slice.call(document.querySelectorAll('.chip'));
  var find = document.getElementById('find');
  var only = 'all';

  function apply() {
    var words = find.value.trim().toLowerCase();
    rows.forEach(function (row) {
      var byWord = !words || row.dataset.text.indexOf(words) !== -1;
      var byKind = only === 'all'
        || (only === 'pick' ? row.dataset.pick === '1' : row.dataset.status === only);
      row.hidden = !(byWord && byKind);
    });
    groups.forEach(function (group) {
      if (group.id === 'picked') { return; }
      var any = group.querySelector('.row:not([hidden])');
      group.hidden = !any;
    });
  }

  find.addEventListener('input', apply);
  chips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      chips.forEach(function (c) { c.classList.remove('on'); });
      chip.classList.add('on');
      only = chip.dataset.filter;
      apply();
    });
  });

  draw();
})();
"""


_CSS = """
/* The product's own palette and type scale - vmd/desktop/style.py - because
   this page is about that product. Its rule travels with it: no state is ever
   carried by colour alone, so every status carries a glyph as well. */
:root {
  --bg: #1B1D20;
  --surface: #27292C;
  --raised: #33353A;
  --line: #45484D;
  --line-strong: #656970;
  --ink: #F4F5F7;
  --muted: #B4B7BE;
  --accent: #EEBB58;
  --ok: #6ED889;
  --warn: #FFBC56;
  --alarm: #FF534B;
  --shadow: rgba(0, 0, 0, 0.35);
  --display: "Segoe UI", system-ui, -apple-system, sans-serif;
  --mono: ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #F6F5F3;
    --surface: #FFFFFF;
    --raised: #EFEDE8;
    --line: #DCD8D0;
    --line-strong: #A9A49A;
    --ink: #1B1D20;
    --muted: #5F6167;
    --accent: #8A6412;
    --ok: #2E7D46;
    --warn: #8A5A00;
    --alarm: #B3241C;
    --shadow: rgba(27, 29, 32, 0.10);
  }
}
:root[data-theme="light"] {
  --bg: #F6F5F3; --surface: #FFFFFF; --raised: #EFEDE8; --line: #DCD8D0;
  --line-strong: #A9A49A; --ink: #1B1D20; --muted: #5F6167; --accent: #8A6412;
  --ok: #2E7D46; --warn: #8A5A00; --alarm: #B3241C; --shadow: rgba(27,29,32,0.10);
}
:root[data-theme="dark"] {
  --bg: #1B1D20; --surface: #27292C; --raised: #33353A; --line: #45484D;
  --line-strong: #656970; --ink: #F4F5F7; --muted: #B4B7BE; --accent: #EEBB58;
  --ok: #6ED889; --warn: #FFBC56; --alarm: #FF534B; --shadow: rgba(0,0,0,0.35);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  padding: clamp(20px, 4vw, 56px) clamp(16px, 4vw, 40px) 96px;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--display);
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.top, .controls, .group, .foot { max-width: 1080px; margin-inline: auto; }

.eyebrow {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 10px;
}
h1 {
  font-size: clamp(28px, 4.2vw, 44px);
  line-height: 1.1;
  font-weight: 600;
  letter-spacing: -0.01em;
  text-wrap: balance;
  margin: 0 0 14px;
  max-width: 20ch;
}
.stand { font-size: 17px; color: var(--muted); max-width: 62ch; margin: 0 0 26px; }
.note {
  max-width: 68ch; color: var(--muted); font-size: 14px;
  border-left: 2px solid var(--line); padding-left: 14px; margin: 22px 0 0;
}
.note strong { color: var(--ink); font-weight: 600; }

.tally {
  display: flex; flex-wrap: wrap; gap: 10px; margin: 0; padding: 0;
}
.tally-item {
  display: flex; align-items: baseline; gap: 10px;
  background: var(--surface); border: 1px solid var(--line);
  border-bottom-width: 2px; padding: 9px 14px;
}
.tally-item dt {
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted);
  display: flex; align-items: center; gap: 7px;
}
.tally-item dd {
  margin: 0; font-family: var(--mono); font-size: 18px; font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.tally-item.is-have { border-bottom-color: var(--ok); }
.tally-item.is-partial { border-bottom-color: var(--warn); }
.tally-item.is-missing { border-bottom-color: var(--line-strong); }
.tally-item.is-never { border-bottom-color: var(--alarm); }
.tally-item.is-pick { border-bottom-color: var(--accent); }
.is-have .glyph { color: var(--ok); }
.is-partial .glyph { color: var(--warn); }
.is-missing .glyph { color: var(--line-strong); }
.is-never .glyph { color: var(--alarm); }
.is-pick .glyph { color: var(--accent); }

.controls {
  position: sticky; top: 0; z-index: 5;
  display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
  padding: 14px 0 12px; margin-top: 34px;
  background: var(--bg); border-bottom: 1px solid var(--line);
}
#find {
  flex: 1 1 260px; min-width: 0;
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--line); padding: 10px 12px;
  font-family: var(--display); font-size: 14px;
}
#find::placeholder { color: var(--muted); }
.filters { display: flex; flex-wrap: wrap; gap: 6px; }
.chip, .button {
  font-family: var(--mono); font-size: 12px; letter-spacing: 0.03em;
  background: var(--surface); color: var(--muted);
  border: 1px solid var(--line); border-bottom-width: 2px;
  padding: 8px 12px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px;
}
.chip:hover, .button:hover { color: var(--ink); }
.chip.on { color: var(--ink); border-bottom-color: var(--accent); background: var(--raised); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.group { margin-top: 44px; }
.group h2 {
  font-size: 13px; font-family: var(--mono); letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--muted); font-weight: 600;
  margin: 0 0 14px; padding-bottom: 10px; border-bottom: 1px solid var(--line);
}
.rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) 150px;
  gap: 14px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 3px solid var(--line-strong);
  padding: 15px 16px;
}
.row.is-have { border-left-color: var(--ok); }
.row.is-partial { border-left-color: var(--warn); }
.row.is-never { border-left-color: var(--alarm); }
.row:has(input:checked) { background: var(--raised); border-color: var(--accent); }
.row h3 {
  font-size: 16px; font-weight: 600; margin: 0 0 5px; line-height: 1.3;
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
}
.star { color: var(--accent); font-size: 13px; }
.row p { margin: 0 0 5px; max-width: 68ch; }
.what { color: var(--ink); }
.why, .who { font-size: 13.5px; color: var(--muted); }
.lead {
  font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--line-strong); margin-right: 5px;
}
.tick { display: flex; align-items: flex-start; justify-content: center; padding-top: 2px; }
.tick input { position: absolute; opacity: 0; width: 0; height: 0; }
.tick label {
  display: block; width: 22px; height: 22px; cursor: pointer;
  border: 2px solid var(--line-strong); background: transparent;
}
.tick input:checked + label { background: var(--accent); border-color: var(--accent); }
.tick input:checked + label::after {
  content: ""; display: block; width: 6px; height: 12px; margin: 1px auto;
  border: solid var(--bg); border-width: 0 2.5px 2.5px 0; transform: rotate(45deg);
}
.tick input:focus-visible + label { outline: 2px solid var(--accent); outline-offset: 2px; }
.meta { display: flex; flex-direction: column; align-items: flex-start; gap: 7px; }
.chip-flat, .inline-chip {
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.04em;
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 9px; border: 1px solid var(--line); color: var(--muted);
  white-space: nowrap;
}
.effort {
  font-family: var(--mono); font-size: 11px; color: var(--line-strong);
  letter-spacing: 0.04em;
}
.sr {
  position: absolute; width: 1px; height: 1px; overflow: hidden;
  clip: rect(0 0 0 0); white-space: nowrap;
}

.chosen { margin: 14px 0 18px; padding-left: 22px; }
.chosen li { margin-bottom: 5px; }
.chosen .in { color: var(--muted); font-size: 12.5px; margin-left: 9px; }
.chosen .none { list-style: none; margin-left: -22px; color: var(--muted); }
.actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.button.quiet { border-bottom-color: var(--line); }
.said { font-family: var(--mono); font-size: 12px; color: var(--accent); }

.foot {
  margin-top: 56px; padding-top: 18px; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 12.5px;
}
.foot p { margin: 0 0 6px; max-width: 72ch; }
.foot code { font-family: var(--mono); font-size: 12px; color: var(--ink); }

@media (max-width: 720px) {
  .row { grid-template-columns: 30px minmax(0, 1fr); }
  .meta { grid-column: 2; flex-direction: row; align-items: center; }
  .controls { position: static; }
}
@media print {
  .controls, .actions, .tick { display: none; }
  body { background: #fff; color: #000; padding: 0; }
  .row { break-inside: avoid; border-color: #999; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
"""


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    out = here / "docs" / "features"
    out.mkdir(parents=True, exist_ok=True)

    body = build_body()

    # The standalone file, which is the one that opens on the offline machine.
    page = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(PAGE_TITLE)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )
    whole = out / "security-features.html"
    whole.write_text(page, encoding="utf-8")

    # The body-only copy, for publishing where the wrapper is supplied.
    fragment = out / "security-features.body.html"
    fragment.write_text(f"<style>{_CSS}</style>\n" + body + "\n", encoding="utf-8")

    rows = _rows()
    counts = _counts(rows)
    print(f"wrote {whole}  ({whole.stat().st_size / 1024:.0f} KB)")
    print(f"wrote {fragment}")
    print(f"  {len(rows)} features across {len(FEATURES)} groups")
    print("  " + ", ".join(f"{STATUS[key][0]}: {value}" for key, value in counts.items()))
    print(f"  picked first: {sum(1 for row in rows if row['pick'])}")
    print(json.dumps({"features": len(rows)}, indent=None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
