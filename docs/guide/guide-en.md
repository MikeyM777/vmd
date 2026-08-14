# VMD - the guide for the person watching the screens

This guide is written for the person who sits in front of this system, not for
the person who built it. It says what to press, what you will see, and what to
do when what you see is not what you expected.

Every picture in this guide has small numbered circles drawn on it. The
numbered list under each picture says what each circle is pointing at.

## What this system is

You have one camera watching a fence line about 700 metres away. This program
is the window onto it. It does three things, all the time, without being asked.

It **shows** you the camera, live, on this screen. It **records** everything
the camera sees, day and night, whether or not anybody is in the room - and it
deletes the oldest footage by itself so that it never runs out of room and
never stops recording. And it **tells you when something moves** in the part of
the picture you have asked it to watch: a red strip appears under the pictures
and a sound plays in the room.

It does not try to say **what** moved. A person, a dog, a vehicle - all of them
are worth knowing about, and the system says only that something moved, and
where, and when. Deciding what it was is your job, and the picture is there so
you can do it.

Two things are worth knowing before anything else.

**The recording is a separate program from this window.** Closing this window
does not stop the recording. Nothing you can press in this window stops the
recording.

**This computer is not connected to anything.** No internet, no wifi. Nothing
leaves this room, nothing is downloaded, and nobody outside this room can see
the camera.

## One camera, one console

There are two cameras, one for each street, and each one has a console of its
own: the same program, opened twice, with a screen each. They share nothing.
The camera address, the recordings, the areas to ignore and the settings all
belong to one console and have no effect on the other.

Everything in this guide describes one console. Do it twice if you need it on
both.

# Starting it

## Normally, after the computer has been restarted

This computer is set up to start on its own. After it is switched on and signed
in:

1. Recording starts within a second or two. It does not need a window open.
2. About 45 seconds later, the consoles open by themselves, one on each screen.

**Wait the 45 seconds.** A blank screen in those seconds looks like a computer
that failed to start, and it is not. If you double-click a shortcut while you
are waiting, you will end up with two consoles for one camera, which is a mess
to sort out and helps nothing.

## By hand

1. Look at the desktop. There are two shortcuts, each named after the street
   its camera watches - the same name that is written above the pictures.
2. Double-click the one you want.
3. The console opens on the screen that camera belongs to. Which screen that is
   is a setting, and you can change it - see **Show on** in the Settings
   chapter.

If a console does not open at all, the machine is still recording. Recording
and the window are separate programs, and one of them failing does not take the
other with it.

# The Live tab

This is the tab you will be looking at nearly all of the time. It is the
pictures from the camera, right now.

![The Live tab](shot:live-tab)

1. The band across the very top, which says whether the system as a whole is
   well: whether it is recording, whether pictures are arriving, whether
   anything is watching for movement, and how the radio link is.
2. The row of tabs, which chooses which page is shown. The page you are on is
   the one with the amber bar under its name.
3. The name of what this camera watches, written in Hebrew above the pictures.
4. The buttons that choose which picture fills the wall: all of them side by
   side, or one on its own.
5. The button that puts the pictures on the whole screen.
6. The plate above each picture, giving the name of that camera view and what
   it is doing right now.
7. One of the two live pictures. Dragging near its edge steers the camera, and
   the arrow keys do the same.
8. The zoom bar belonging to that picture. There is one for each lens.
9. The Link panel, which says in one word how the radio link is doing.
10. The Storage panel: how much of the allowed space the recordings have used,
    and how long before the oldest starts being deleted.
11. The Movement line, which counts what has moved today and when the last one
    was.

## Which console is this?

Number 3 above is the whole answer, and it is worth a paragraph of its own.

Two consoles are open on this desk, side by side, showing two treelines that
look alike at three in the morning. The name above the pictures is the street
this camera watches, in your own words, and it is the one thing on the screen
that tells the two apart. It is on the window's title bar as well, and it stays
on the screen in fullscreen. Steering the wrong camera loses the perimeter you
were watching, and you would not know you had.

You set it yourself: **Name**, under **Camera**, on the Settings tab.

## Choosing which picture you see

![The buttons above the pictures](shot:view-chooser)

1. The name of what this camera watches, in Hebrew.
2. The button that shows every camera view side by side. It is the one that is
   on here, and it is marked by the amber bar beneath it.
3. A button that gives one camera view the whole wall on its own. There is one
   for each view the camera has.
4. The button into fullscreen, where only the pictures are left.

The number keys do the same thing: **1** shows all of them, **2** shows the
first view, **3** the second, and so on.

Showing one view on its own stops the other one being decoded, which leaves the
computer more room to do its job. Nothing stops being recorded: what you choose
to look at has no effect at all on what is written to the disk.

The choice is remembered. Tomorrow morning it opens on the same view.

## Steering the camera

The camera turns, tilts and zooms, and you drive it from this tab.

- **The arrow keys** turn and tilt it. The camera keeps moving while the key is
  held down, and stops when you let go.
- **Hold Shift** with an arrow key to move slowly, for the last small
  correction.
- **+ and -** zoom in and out.
- **Home** brings the camera back to its home position.
- **Dragging on the picture** steers as well: press the mouse down near an edge
  of the picture and the camera moves that way, faster the nearer the edge you
  are. The middle of the picture does nothing. Let go and it stops.

The arrow keys work while the Live tab is the page you are on. If you go to
Settings or Logs, the camera stops - deliberately, so that a key held down when
you change tabs cannot leave the camera turning with nobody watching.

## The column of readings

![The column beside the pictures](shot:live-side-column)

1. What the camera head is doing right now: `idle`, or the speed it is being
   driven at.
2. The keys that steer the camera, written out.
3. The one word for the state of the radio link: `GOOD`, `FAIR`, `BUSY`,
   `FULL`, `WEAK` or `NO LINK`.
4. The two bars under it: how strong the signal is, and how much of the link is
   being used, each with its limits marked on the track.
5. How much space the recordings have used out of what they are allowed, and
   how long there is before the oldest is deleted.
6. The movements counted today. Pressing this line opens the footage of the
   newest one, when the Playback tab is switched on.

Under **Steering** there is one more line that appears only when there is
something wrong, most often
`the camera did not answer the last command yet`. That is the camera being slow
over the radio link, not the console being broken. If it stays up, look at the
troubleshooting table at the end of this guide.

## The zoom bar

There is one zoom bar under each picture, because this camera is really two
cameras on one mounting - a heat camera and an ordinary one - each behind its
own lens.

![The zoom bar under a picture](shot:zoom-bar)

1. The minus button, which zooms this lens out to a wider view.
2. The handle, which sits where the camera says the lens actually is. Dragging
   it sends the lens somewhere.
3. The plus button, which zooms this lens in closer.
4. The reading: the word `zoom` and how far in the lens is, as reported by the
   camera itself and never guessed.
5. The bottom of the picture this zoom bar belongs to. The bar sits inside that
   picture's own frame, and the other picture has one of its own.

That the reading comes from the camera matters: a lens that has stopped because
it is at its limit and a lens that has stopped because the command never
arrived look exactly the same through a picture that is not changing, and only
the camera can tell you which it is.

Two things the bar may say instead of a percentage:

- `checking the lens` - nobody has asked the camera yet. This is normal for the
  first few seconds after the console opens.
- `zoom not reported` - the camera was asked and says it cannot report where
  its zoom is. The buttons still work; the slider simply cannot show a position
  that was never sent.

`zoom 0% wide` is as wide as the lens goes; `zoom 100% tele` is as close as it
goes.

If the camera has one lens behind both pictures, a line under the pictures says
so, and either zoom bar moves the same glass. That is what the camera reported,
not a fault.

If the zoom bar under one picture moves the **other** picture, there is a
one-press fix in Settings - see **Swap them**.

## Fullscreen

Press **F11**, or the button at the top right of the pictures. The pictures
fill the whole screen; the band across the top, the tabs and the column of
readings all go away.

![Fullscreen](shot:fullscreen)

1. The name of what this camera watches, still on the screen in fullscreen: it
   is the one thing that says which of the two consoles you are looking at.
2. The way back out, which names its own key. **Esc** and **F11** do the same.
3. The view buttons, which are kept in fullscreen.
4. The zoom bars, which are kept in fullscreen too.

Three ways out, and all three work: press **Esc**, press **F11** again, or
press that button.

Steering still works in fullscreen. Movement is still watched for, and the red
strip still appears.

# When something moves

## What happens

Three things happen at once, the moment movement is confirmed.

1. A **red strip** appears under the pictures, saying which view it was seen on
   and at what time.
2. A **red outline** is drawn round the picture it happened on, so with two
   pictures side by side you do not have to work out which.
3. A **sound** plays in the room.

![The Live tab with an alarm up](shot:live-alarm)

1. The red strip that appears under the pictures when something has moved,
   saying which camera saw it and at what time.
2. The red outline drawn round the picture the movement was seen on.
3. The button that clears the strip and the red outline once the movement has
   been seen.
4. The same movement counted in the column beside the pictures, where it stays
   after the strip has been cleared.

![The strip, close up](shot:alarm-strip)

1. The filled bar drawn beside the words, so the alarm is not carried by the
   colour alone.
2. Which camera view the movement was seen on, and the time it happened.
3. The button that takes the strip down again.

When the Playback tab is switched on there is a second button on the strip,
**Show me**, which takes you straight to the recording of that movement.
Normally the Playback tab is off and the button is not there.

## About the sound

It is a short sound, and it will not sound more than once every twelve seconds
however much is moving. A windy night is one sound, not forty. You can switch
it off in Settings, under **Movement detection** - do that if somebody sleeps
in this room, rather than unplugging the speakers, because speakers that have
been unplugged are silent for good and nobody remembers why.

## What "Seen it" does, and what it does not do

**Seen it** means one thing: you have seen the notice. It takes the red strip
and the red outline off the screen.

It does **not** stop the recording. Nothing here does.

It does **not** delete anything. What moved is still recorded, still on the
disk, and still counted in the **Movement** line in the column beside the
pictures.

It does **not** mean whatever moved has gone away. The camera has no opinion
about that. If something is walking along the fence, the strip clearing tells
you nothing except that you pressed a button.

If the strip clears on its own, that is not the system deciding all is well
either - it is the next movement replacing the last one.

## The Movement line

In the column beside the pictures, under **Movement**, one line says what has
happened:

- `3 movements, the last at 14:02 - press to watch it` - press it to see the
  most recent one, if the Playback tab is switched on.
- `Nothing has moved yet.` - nothing has moved, and something is watching.
- `Nothing is being watched for movement.` followed by how to turn it on -
  nobody has asked for any view to be watched, so an empty list means nothing.
- A longer line saying nothing is watching **right now** - something is broken,
  and the band across the top says what.

Those last three are different sentences on purpose. An empty list because
nothing happened and an empty list because nobody is looking are not the same
news, and you should never have to guess which one you are reading.

# The band across the top

The strip across the top of the window, above the tabs, is the health of the
whole machine. It is true of the machine and not of whichever tab you have
open, so it is there on every tab.

![The band with nothing wrong](shot:status-band-healthy)

1. The recording dot, which pulses while footage is reaching the disk.
2. Each part of the system, named on its own when there is nothing wrong with
   it: recording, streaming, detection, link.
3. The radio link, healthy. The figure behind it is on the Live tab.

When everything is well, each of these is just its own name in ordinary
lettering with a small green mark beside it. Nothing is boxed, and nothing
shouts.

![The band with something wrong](shot:status-band-trouble)

1. The same dot, now a still red bar instead of a pulsing circle: nothing is
   being recorded.
2. The one thing that is wrong, spelled out in full inside a red box. Only the
   worst fault is given the room to explain itself.
3. The radio link, busy rather than failed: marked in amber, and keeping only
   its short name.

The dot is worth looking at twice. **What separates recording from not
recording is the movement, not the colour** - a pulsing circle when footage is
reaching the disk, a still bar when it is not. A glance that lands on the dim
beat of the pulse cannot be mistaken for a stopped recorder.

When more than one thing is wrong at once, the box goes to the one that
explains the others - there is only one line of room, and the sentence worth
reading is the one nearest the cause. The others keep their mark and their
colour, so you can still see there is more than one.

## What each word means, and what to do

| What the band says | What it means | What to do |
|---|---|---|
| `recording`, with a pulsing dot | Footage is reaching the disk. | Nothing. This is the normal state. |
| `NOT recording - ...` | No footage is reaching the disk, and the rest of the sentence says why. | Read the rest of the sentence. The commonest causes are in the troubleshooting table at the end. |
| `streaming` | Pictures are arriving into this computer. | Nothing. |
| `no pictures`, or `part of VMD is missing, so there are no pictures. Reinstall VMD.` | Part of the program is not on the disk. | This one needs the person who maintains the system. Send them the logs (see the last chapter). |
| `no stream addresses set - enter them in Settings` | Nobody has told this console the camera's picture addresses yet. | Settings, the **Streams** cards, then **Save**. |
| `detection` | Something is watching for movement. | Nothing. |
| `no detection` | Movement detection is switched on but is not running. | Look at the Logs tab. If it does not come back on its own, send the logs on. |
| `detection: off - no stream has detection enabled` | Nobody has asked for any view to be watched. This is quiet, not red - it is a setting, not a fault. | If you want to be told about movement, tick **Watch ... for movement** on a camera card in Settings and press **Save**. |
| `link` | The radio link is fine. | Nothing. The actual signal figure is in the **Link** panel on the Live tab. |
| `link FAIR` | Working, without much margin left for rain or a mast that has shifted. | Nothing right now. Mention it to whoever maintains the system. |
| `link BUSY` | The link is nearly full. The picture may stutter and the camera may be slow to answer the arrow keys. | It usually clears. If it stays, see the troubleshooting table. |
| `link FULL` | Nothing else fits down the link. Pictures stutter or drop when the camera pans. | See the troubleshooting table. |
| `link WEAK` | The signal is close to the noise. The picture can break up. | Weather, or something has moved the aerial. Report it. |
| `no link` | The radio cannot be read at all. | Recording and the picture may still be fine - the radio is only how VMD measures the link. Check the address and password under **Radio** in Settings. |
| `no thermal`, `no visible`, `no pictures (2)` | That picture has stopped arriving. The name is the view's own name. | Troubleshooting table, first row. |
| `thermal frozen` | That picture is still connected but no new frames are arriving. What you can see is the last one that came. **This is the dangerous one**: a frozen picture looks exactly like a quiet perimeter. | Troubleshooting table. |
| `sent twice` | The link is carrying the same camera picture more than once, which wastes room on a link that barely has enough. | Nothing has failed. Tell whoever maintains the system. |
| `VMD cannot see its own recorder and detector. Restart VMD.` | The window has lost track of the programs it started. | Close the window and open it again from the shortcut. Recording is not affected by closing the window. |

# Settings, field by field

Everything this console can be told is on the Settings tab. Nothing on it takes
effect until you press **Save**.

Two rules before the fields:

- **Nothing is written until you press Save.** You can look at everything on
  this tab and change your mind by moving to another tab.
- **Ctrl+S saves too.** The Save button is at the very bottom of a long page,
  and Ctrl+S saves from anywhere on it.

## Camera

![The Camera box](shot:settings-camera)

1. The name of what this camera watches, in your own words. It is written above
   the pictures and on the window itself.
2. Which monitor this console opens on, so that the two consoles do not land on
   top of each other after a restart.
3. The camera's address on the network.
4. The username the camera expects.
5. The camera's password, shown as it was typed and never hidden behind dots,
   so that a mistyped one can be seen and corrected.

**Name** is worth a moment. A street, a gate, a direction - Hebrew is what it
is for. Leave it empty and nothing is written above the pictures.

**Show on** has one more choice on the list: "Wherever it was last left", which
remembers where you dragged the window. That is the right answer on a machine
with one screen, and the wrong one on this desk, where after a restart both
consoles come back at once.

### Why the passwords are not hidden

Every password on this tab is shown as you typed it. That is deliberate, and it
is worth understanding, because it looks like a mistake.

This computer has no internet and no wifi, it stands in a locked room, and only
somebody standing at this desk can reach it. There is nobody for a hidden
password to be hidden from. What this form really suffers from is a typed
password nobody can see: a wrong camera password means no picture and no
recording, and a hidden field makes that mistake invisible at the exact moment
it matters most.

So they are shown, and you can read back what you typed.

## Streams - the camera's views

The camera sends more than one picture: a heat picture and an ordinary one.
Each of them gets a card.

![The camera's views](shot:settings-streams)

1. The name of one camera view. It is the name this view goes by everywhere
   else in the console.
2. The address the camera serves that view on.
3. The tick that watches this view for movement. It is ticked here, so this
   view is being watched.
4. The same tick on the other view, unticked: no view is watched for movement
   until it is asked for.
5. The button that shows a picture from this view so that parts of it can be
   drawn round and ignored.
6. A fold holding the one setting that makes a view more or less touchy. It is
   shut until it is needed.
7. The button for when the zoom bar under one picture moves the other picture.
   It exchanges the two lenses.

The **name** is not decoration: it is the name on the picture on the Live tab,
the folder the recording goes into, and the word the movement alarm uses. Two
views cannot share a name.

The **address** begins `rtsp://`. It is the one field on the whole tab that is
fiddly, and if you do not have it there is a button that hunts for it - see
**Check the camera** below.

The tick that watches a view carries that view's own name, so with two cards
side by side you can never tick the wrong one. Recording carries on either way;
this only decides whether you are told.

The fold at (6) is called **Advanced**, and it holds **How touchy:** with three
positions: Low (only big, obvious movement), Normal, and High (notices small or
distant movement). Start at Normal, and leave it alone unless this view alarms
too much or too little.

**Swap them** is one press for one specific fault: the zoom bar under one
picture moves the **other** picture. Press it, press **Save**, and try the
sliders again. Press it again to put them back. Nothing else about the camera
is changed.

There is one more control that appears only if your camera has said it has more
than one lens: **Zoom drives**, a chooser on each card. Leave it on "Work it
out for me" unless a zoom slider moves the wrong picture or does nothing at
all. **Swap them** is the easier answer to the same problem.

The list of views is fixed. You cannot add one and you cannot remove one, on
purpose: this camera has two heads, and a stray click that removed one would
cost you a camera with no way to get it back from this screen.

## Parts to ignore

This is the one thing on the Settings tab that is done with a mouse and a
picture instead of with words.

Movement detection reports anything that moves. Pointed at a hillside with
nothing marked, it will report the trees all night, and an alarm nobody
believes is worse than no alarm at all. So you draw round the things you do not
want reported.

![Marking the parts of a picture to ignore](shot:mask-dialog)

1. The instruction: drag around anything that should be ignored, and click a
   marked area to take it off again.
2. An area already marked out, shaded in red over the picture. Movement inside
   it is never reported.
3. A second marked area, this one dragged as a box rather than drawn round.
4. The tool that draws round a shape freehand. It is the one that is on, marked
   by the amber bar beneath it.
5. The tool that drags a rectangle instead.
6. The button that takes off the area drawn last.
7. The button that takes off every marked area at once.
8. The button that leaves the marked areas exactly as they were.
9. The button that keeps what was drawn. **Save** on the Settings tab is what
   writes it to the file.

Draw round a treeline freehand rather than boxing it. A treeline is a ragged
band, and a box round it either throws away the sky above it or leaves half the
branches watched. Use the box for the things that really are a box: the sky, a
road.

Everything outside what you drew is still watched. There are no numbers on this
window on purpose - a part of a picture is not a thing you can describe in
numbers you could check.

If the camera cannot be reached when you open this, it opens anyway with a
sentence saying so, and the areas you have already drawn are still there to
delete.

## Movement detection

![Movement detection and the alarm sound](shot:settings-detection)

1. The master switch: with it off nothing is watched for movement whatever the
   camera views say, and recording carries on either way.
2. The switch for the sound an alarm makes as well as the red strip. It never
   sounds more than once every twelve seconds, and it can be turned off if
   somebody sleeps in the room.

Which views are watched, and how touchy each one is, is set on the camera cards
above, not here.

## Playback

The Playback tab is **off**. It is not a fault and nothing is missing.

![Switching the Playback tab on](shot:settings-playback)

1. The tick that adds the Playback tab to the console. The tab is off normally,
   and this tick stays down until the question below it has been answered.
2. The "Are you sure" pane the tick raises, which says what switching the tab
   on brings back with it.
3. The button that agrees, which is what actually switches the tab on.
4. The button that leaves it off and takes the question away.

Ticking it does not turn it on by itself. The tick goes back down and the
question appears; the tab only comes back if you answer **Yes, show it** and
then press **Save**. That is on purpose: a tab appearing out of nowhere on a
console you have learned is a bigger surprise than the tick that caused it
looks.

Turning it on changes nothing about the recording. Recording is a separate
program, it is not told, and the footage is on the disk either way.

## Storage

![Where the recordings go and how much room they may take](shot:settings-storage)

1. The folder the recordings are written to.
2. The button that looks at the drive and fills in a size and an age rule to
   match it. It changes the two boxes below and nothing else, and nothing is
   written until Save.
3. How much of the drive the recordings are allowed to fill. The slider stops
   at the size of the drive.
4. The same size as a number, which can be typed instead of dragged.
5. What that size means in days of footage.
6. The age rule: anything older than this many days is deleted whether there is
   room for it or not. Empty means nothing is deleted because of its age.
7. The line saying which of the two rules is the one actually deleting footage
   today.

The slider and the box are the same setting; the box is the setting and the
slider is a way of moving it. The slider stops at the size of the drive because
a size bigger than the drive is a size nothing can ever reach.

With the age rule left empty, footage goes only when the space above is full,
oldest first.

### The one thing on this screen that destroys something

**Lowering the size the recordings are allowed to fill deletes footage, and it
cannot be undone.**

Type 10 where 100 was, press Save, and about 90 GB of your oldest recordings
go. So this one asks twice: the first press of Save does not save. Instead the
line beside the button says how much footage is about to be deleted, and you
have to press Save a second time to go ahead. Correct the number and the
question is asked again about the new one.

Raising it, leaving it alone, and lowering it to something the folder still
fits inside all save on the first press, like everything else.

If you type a size bigger than the drive, Save refuses it and says so. That is
not fussiness: old footage is deleted only once that size is used up, so a size
bigger than the drive means nothing is ever deleted and the drive fills up
until recording stops.

## Radio

The radio is the aerial link between this building and the camera. VMD does not
need it to record - it needs it to be able to tell you how the link is doing.

![The radio link](shot:settings-radio)

1. The radio's address. Without it the **Link** panel on the Live tab has
   nothing to read.
2. The radio's username.
3. The radio's password, shown as typed like every other password here.
4. The switch that lets the console ask the camera for a smaller picture by
   itself while the link is busy, and for a better one again once it has been
   quiet.

Leave that last switch ticked unless you have a reason not to: every serious
problem this system has had has been the link filling up. It changes things
rarely, and each change makes the picture jump for a moment.

The line under it says the lowest picture the camera will ever be asked for. It
never goes below that; if the link cannot carry even that, it says so in the
Logs tab rather than spoiling the picture further.

## Check the camera

![The tools for checking the camera](shot:settings-camera-tools)

1. The fold holding the camera tools. It is shut until it is pressed, and
   nothing inside it changes a setting.
2. The button that asks whether the camera is answering at all.
3. The button that hunts for the camera's address when nobody knows it. It
   takes up to a minute.
4. The button that asks which lens is behind which picture. It also fills in
   the **Zoom drives** chooser on each card.
5. The button that asks the camera for a smaller picture, once, when the link
   cannot carry the one it is sending.
6. The button that writes everything it found into a file that can be sent to
   somebody. **No password is ever put in that file.**
7. The box the answers appear in.

You do not need any of this unless something is wrong.

## Save

![Saving, and what it says when it will not](shot:settings-save)

1. The line that appears when a setting was refused, saying which one and why.
   Nothing at all was saved while this is showing.
2. The button that writes these settings to the file. Nothing typed on this
   page takes effect until it is pressed, and **Ctrl+S** does the same.

Pressing Save writes the file and then restarts the parts of the system that
need to hear about it, which takes a few seconds. The line beside the button
tells you what is being done while it is being done, and then tells you whether
it all took. Read it. `Saved.` on its own means everything took; `Saved, but`
means the settings are in the file and something did not restart, and the Logs
tab will say more.

# The Playback tab

## It is off

There is no Playback tab on this console unless somebody has switched it on. It
was taken off the screen because this console is for watching the camera now.

**The recording is untouched by any of this.** The footage is on the disk, it
always has been, and this tab is only the window onto it.

## Turning it on

1. Go to the **Settings** tab.
2. Find the **Playback** box.
3. Tick **Show the Playback tab**.
4. A question appears. Press **Yes, show it**.
5. Press **Save**.

The tab appears immediately, second along, between Live and Settings. You can
switch it off again in the same place at any time.

Turning it on or off changes nothing about the recording. It is a separate
program, it is not told, and the files are where they were.

## Using it

![The Playback tab](shot:playback-tab)

1. The day being looked at. The buttons either side of it step one day back and
   one day forward.
2. Which camera view is being looked back through.
3. The clock: the moment the picture below it is showing.
4. How much of the day the bar at the bottom covers: the whole of it, an hour,
   or ten minutes.
5. The bar for the day. The filled green parts are what was recorded, and the
   gaps are the times nothing was.
6. The marks on the bar where something moved. Pressing one plays the footage
   of it.
7. Play and pause.
8. How fast the footage is played back.
9. The start of a piece of footage to keep. **Mark end** beside it is the other
   end of it.
10. The button that writes the marked piece to a folder you choose.

Nothing you can press on this tab deletes any footage.

# The Logs tab

## What it is for

The Logs tab is the last few hundred things this system said about itself - the
console, the recorder, the part that fetches the pictures and the part that
watches them for movement, all in one list, newest at the bottom.

It is for two moments, and only two:

- **When something is wrong** and the band across the top does not say enough.
  The sentence that explains a fault is almost always here.
- **When you need to tell somebody else** what is wrong. This is what you send.

![The Logs tab](shot:logs-tab)

1. The button showing every line. It is the one that is on, marked by the amber
   bar under it.
2. The button that hides everything except warnings and errors.
3. The button that copies the lines shown, so they can be pasted somewhere and
   sent to somebody.
4. The tick that keeps the table on the newest line as lines arrive. Untick it
   to stay where you have scrolled to.
5. The four columns: when the line was written, how serious it is, which part
   of the system said it, and what it said.
6. A line in red, which is an error. This one is the camera refusing the
   username and password in Settings.

Passwords are taken out of every line before it is shown, so anything you copy
from here is safe to send on.

The list holds the last 500 lines and no more. If something went wrong an hour
ago and a lot has happened since, it may already have scrolled out of the list -
which is a reason to copy it when you see it rather than later.

# When something is wrong

Find what you can see in the left-hand column.

| What you see | What it means | What to do |
|---|---|---|
| A picture is black and its name says `failed` | That view is not arriving. The console keeps trying, and waits longer between each attempt. | Wait a minute - most of these come back on their own. If the name changes to `failed - not coming back on its own; check the address in Settings`, the console has stopped believing its own retries: check that view's address on the Settings tab, and that the camera has power. |
| A picture is black and its name says `connecting` | It is trying. | Wait. If it stays like this for more than a minute, treat it as `failed` above. |
| A picture has not changed for a while and its name says `late - no new pictures` | The connection is alive but no new frames are coming. **What you are looking at is the last picture that arrived, not the perimeter now.** | This is usually the radio link. Look at the **Link** panel. If the link is `BUSY` or `FULL`, see below. If it stays late with a healthy link, send the logs on. |
| There are no pictures at all and the wall says `No pictures. Add a camera view in Settings.` | This console has not been told any picture addresses. | Settings, fill in the address on each camera card, **Save**. |
| The band says `NOT recording` | No footage is reaching the disk. Read the rest of the sentence - it names the reason. | `no stream is ticked to record`: nothing is set up yet, so fill in the Streams cards. `nothing has been written for ...`: the recorder is failing; look at the Logs. `restarted N times`: it keeps dying, which the Logs will explain. `the recordings folder ... is not there` or `cannot be reached`: the folder in Settings is wrong or the drive is not there. |
| The band says `NOT recording - the newest recording is dated later than this machine's clock` | This computer's date and time are wrong. Nothing can be trusted about what was recorded when until it is fixed. | The date and time need putting right on the computer itself. This is one to hand on. |
| The link says `BUSY` or `FULL` | The radio link is nearly full or completely full. The picture stutters and the camera answers the arrow keys slowly, because your steering has to queue behind the video. | Make sure the switch that turns the picture down by itself is ticked under **Radio** in Settings, and press Save. Showing one view instead of both also helps. If it stays `FULL` with nothing changing, hand it on. |
| The link says `WEAK`, or `NO LINK` | The signal is close to the noise, or the radio cannot be read at all. | Not something you can fix from this screen. Note whether it happened in bad weather. Send the logs on. Recording may still be fine - check the recording dot. |
| The **Storage** panel says the budget is full | Normal, and not a fault. The oldest footage is being deleted to keep recording going. This is what the setting is for. | Nothing, unless you want to keep more: raise the size in Settings. |
| The **Storage** panel says the drive will run out before the budget is reached | This one **is** a problem. Old footage is only deleted when VMD's own size limit is passed, so if the drive fills first, recording just stops with every rule reporting itself satisfied. | Lower the size in Settings until it fits, or get space freed on that drive. The button that looks at the drive will work out a size that fits. |
| The zoom bar under one picture moves the other picture | The console guessed which lens is behind which picture, and guessed wrong. It is a known thing and there is a one-press fix. | Settings, under the camera cards: **Swap them**. Then **Save**, then try the sliders again. Press it again to put them back. |
| A zoom bar does nothing at all and its reading says `zoom not reported` | The camera will not say where its zoom is. | The **+** and **-** buttons still work. If neither does anything, use the button that asks which lens is behind which picture, under **Check the camera**, and hand the answer on. |
| The **Steering** panel says `the camera did not answer the last command yet` | A command has been sent and the camera has not replied. Over a radio link a second or two is ordinary. | If it clears, nothing. If it stays up while the picture is still arriving, the camera is refusing commands: hand it on. |
| The band says `sent twice` | The same camera picture is crossing the radio link more than once, wasting room on a link that barely has enough for one. | Nothing has failed and nothing needs doing this minute. Hand it on. |
| The band says `VMD cannot see its own recorder and detector. Restart VMD.` | This window has lost track of the programs it started. | Close the window and open it again from its shortcut. Closing the window does not stop recording. |
| Everything looks fine but the picture is frozen | See `late - no new pictures` above. A frozen picture is the one failure that looks like a quiet perimeter, which is why the band shouts about it. | Check the name above the picture. It is the line that tells the truth. |

# When you do not know what to do

## Two things to send

Whoever maintains this system can do almost nothing without these two, and you
can produce both from this window in under a minute.

**The logs.**

1. Go to the **Logs** tab.
2. Press **Warnings and errors**.
3. Press **Copy**.
4. Paste it into a message.

**The report.**

1. Go to the **Settings** tab and scroll to the bottom.
2. Open **Check the camera**.
3. Press **Save a report** and choose where to put the file.
4. Send that file.

Neither of them contains a password. Both are safe to hand on.

Say what you saw as well: which part of the band was red, what the name above
the picture said, and roughly when it started. The time matters, because the
logs hold only the last 500 lines.

## What you cannot break

It is worth knowing what is safe, so that being unsure does not stop you
looking.

- **Closing the window does not stop the recording.** The recorder is a
  separate program. Close the console, reopen it, and it is still recording.
- **Nothing in this console deletes recorded footage** - with one exception:
  lowering how much space the recordings are allowed to fill, in Settings,
  which deletes the oldest footage to fit the new size, and which asks you
  twice before it does.
- **Turning the Playback tab on or off deletes nothing and changes nothing
  about the recording.**
- **Turning movement detection off deletes nothing and stops nothing being
  recorded.** It only stops you being told.
- **Switching between views, going fullscreen and steering the camera** change
  nothing on the disk at all.
- **Pressing the buttons under "Check the camera" changes no setting.** They
  ask questions and print the answers.
- **Looking at Settings changes nothing.** Nothing is written until you press
  **Save**.

The one thing that is worth being careful about is the size the recordings are
allowed to fill, because lowering it throws footage away. Everything else on
this screen can be tried, looked at and undone.
