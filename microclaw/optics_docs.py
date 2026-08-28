OPTICS_REFERENCE = """
# Optical-path reference: how light paths usually work

## Usual path ordering — and why the actual stand may differ

How light paths usually work: a common transmitted-light ordering is lamphouse →
filters → field diaphragm → condenser → specimen → objective → beam splitter or
prism → eyepieces or a trinocular/camera port. This is an orientation vocabulary,
not a claim about the current rig: inverted stands put the condenser above and the
objective below the specimen; epi-illumination sends excitation through the
objective with no substage lamp in that path; TIRF, spinning-disk, optosplitter,
and multi-camera stands change the graph. Read `optical_path`, which reports the
actual motorized devices, and ask the operator about components it cannot report.

Ports may be called eyepiece, side/left/right, bottom/base, or front. Split labels
may contain 100, 80, or 20. `4-Left80` establishes at most that this adapter
describes an 80% route toward its left port; it does not establish that a camera
is attached there or where the remaining light goes. A percentage is routing
vocabulary, not necessarily a position index. Ask the operator which detector is
attached to each physical port.

## What software can see

Micro-Manager can report the motorized path only. A manual prism slider, a filter
cube pulled to a detent, a closed field diaphragm, or a condenser out of position
can make the camera dark while every readable value is correct. With a blank frame
and a valid focus lock, check the manual prism first, then ask the operator about
the rest of the physical route. Use `optical_path` for the devices actually read.

## Objectives and focus locks

An objective is described by magnification, numerical aperture, and immersion
medium; working distance and a sensible focus-search window scale with those
properties. An unnamed turret state such as `4-Unknown` means only that the
configuration author did not label it, not that the turret is broken. Ask the
operator which objective is seated unless `optical_path` and calibration data
actually identify it.

Hardware focus locks generally use reflection from the coverslip, acquire only
inside a capture band, and hold a chosen offset after capture. A lock can remain
engaged on the wrong reflecting surface. Treat its reported engagement as a lock
reading, not proof that the specimen plane or optical route is correct.
"""
