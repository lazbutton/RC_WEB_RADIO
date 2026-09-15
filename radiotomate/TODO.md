# Things TODO for Radiotomate

This is a roadmap and a support for ideas and a priorization helper.
When things are done, they're moved to [CHANGELOG.md](CHANGELOG.md).

## documentation plan

Split the doc in three:

1. admin documentation, write with Pandoc. Intro, howto install (including an interactive install.sh invocation generator), configure, backup-restore
2. user doc: embed in the app as tooltips that can be disabled per-user. In perticular, explain the scheduling logic below the carts form, and the autoDJ logic below the AutoDJ form.
3. dev doc: use pdoc3

## Things to dispatch in upcoming documentation

Philosophy: refer to Dillo's [take on complexity](https://dillo-browser.github.io/complexity.html),
htmx's [building the 100-year service](https://www.youtube.com/watch?v=lASLZ9TgXyc).

be careful we're on APScheduler @master/vAlpha https://apscheduler.readthedocs.io/en/master/userguide.html

note in doc that Path (in Cart and Sound) are stored relative to DATA_ROOT,
but absolute when instanciated in Python.

architecture page should mention that we don't try to support browsers without javascript,
to simplify the implementation and many details in the interface need Javascript anyway.
So we use htmx extensively. Even on a classic POST,
where we have to return an `hx-redirect` header instead of a `redirect()`.
Although this is not much RESTful,
it makes error management much simpler:
4XX responses' messages are used as alert messages.

The scheduler trick is that the scheduler should choose to push jingles/music according to queue counters coming from /live,
so when the future "queue cleaner" is implemented, it will only have to delete old items from queues

on max duration: warn that the max is scheduled relatively to the cart's schedule, who might not be applied right away - a jingle can be inserted, for example.

autodj = "bande continue" en français

note that the auto-DJ falls back to "everything" when filters are too restrictive

replaygain follows [beets configuration](https://docs.beets.io/en/latest/plugins/replaygain.html#configuration)
so configure it in beets first.
 - computation happens asynchronously, and the sound will be marked unavailable until computed.
 Warn that adding a sound to a cart that will play 3 seconds later might be too short

 - document the debugger trick: launch ${workspaceFolder}/radiotomate/commands/main.py
 - to run end-to-end tests, firstly run `playwright install`
 - hey there's PWDEBUG=1 pytest -s !!! also, use `interface --reload --verbose`
 - front-end: mention accessibility, because it also helps for end-to-end testing

Installation doc: if you want to have multiple radios sharing the same music library, install beets first.
 - on the landing page, mention this as a feature

Installation requirements: if you want to go with the fully automated installer, that
relies on Podman quadlets, you'll need at least Podman 4.4 - this rules out Debian
12/bookworm, [who's stuck on Podman 4.3](https://packages.debian.org/search?keywords=podman).

Install: do not put "localhost" in the configuration file, put "host.containers.internal" instead.

## Missing Tests

bug:
```
[07/25/25 14:20:32] ERROR    [radiotomate.interface_app:1115] Exception                                                                                           app.py:1115
                             Traceback (most recent call last):
                               File "/home/martin/.pyenv/versions/radiotomate/lib64/python3.13/site-packages/quart/app.py", line 1322, in _wrapper
                                 await self.ensure_async(func)(*args, **kwargs)
                               File "/home/martin/radiotomate/radiotomate/interface/live.py", line 63, in watch_livedata_task
                                 remaining = round(float(md.get("remaining", 0.0)))
                             OverflowError: cannot convert float infinity to integer
```
* time two tracks identically ? what happens ?
* unit test push beets ??
* unit test POST /live doesnt crash (it does) when there's a jingles cart, but empty
* how goes a browser's RAM consumption when left open on /live for a long time ?
* a cart with 200 sounds - is everything still fast ?
* `radiotomate install --load-test` to pre-fill a complete programs grid + sounds + users for perf tests.
* when adding many sounds in a cart, the spinner on "add sounds" in the sounds table is ambiguous
* support empty metadata_log blocks in config

howto load test with `oha` (`cargo install oha`) :

```
oha -n 200 -c 1  --latency-correction --disable-keepalive \
-H "Cookie: session=...." \
http://127.0.0.1:2345/users
```

Test streams:
 * https://radiocampus.fr/audio/live/radio-campus-france
 * https://live.campusgrenoble.org/rcg112

## Refactoring

* when downloading a song from the library or carts, its MIME type should not always be `audio/mpeg`

## Installer

podman run --rm -it --security-opt label=disable \
  -e PULSE_SERVER=unix:$XDG_RUNTIME_DIR/pulse/native  \
  -v $XDG_RUNTIME_DIR/pulse/native:$XDG_RUNTIME_DIR/pulse/native \
  -e RTCONFIG=/radiotomate/radio.yaml \
  -v /home/martin/radiotomate:/radiotomate:z \
  savonet/liquidsoap:v2.4.0 /radiotomate/radiotomate.liq

Manual mode =
 - pip install radiotomate
 - opam install liquidsoap
 - radiotomate install
 - download .liq

Secure :
* Security headers: have a look at https://github.com/TypeError/secure
* SSL : install certbot (it's python too), add an installer method and an adequate webapp/endpoint to generate certs in "data/", domain could be set from "settings" ?
* set Quart param PREFERRED_URL_SCHEME=https in that case

## v0.1 - first demo-able version

* documentation (see above)
* create Cart.schedule_summary(), that computes what's displayed in the "Schedule" column of the Carts page.
* set delay between jingles in config file - will help for:
* bug: last play time is not always saved correctly (for jingles in perticular)
* push jingles in the autoDJ source only
* bug: transitioning from cart to auto-DJ does not start a new track (consequence: /live is not correct)
* when testing on debian: if weird things happen, especially on reboot, look at https://github.com/containers/podman/issues/25382
* let installer pre-configure outputs, try alsa too
* bug: when editing a cart, don't show the queue when cart mode is Relay
* refacto: write service and pod's YAML file in the data folder, link them in the systemd folder.
* max_duration available via a checkbox
* refacto: separated forms for relays
* refacto: separated forms for jingles
* add help line below cart modes "how do we choose sounds from cart"
* add help tooltip explaining how the autoDJ works
* admin doc should mention that the scheduling system lets you do errors: keep an eye on the playlist page. So add a basic playlist page (like, past 1000 entries?)
* install (and document) dropbox container

before releasing:
* poetry check --lock
* separate README for Pypi, modernize the pyproject.toml
* dependency update and release for drop2beets too
* reset update/ folder
* embed license and contributor guidelines in the app + link that in the footer everywhere + AGPL says "if your program is a web application, its interface could display a "Source" link that leads users to an archive of the code"
  - use the FSFE's [reuse-tool](https://github.com/fsfe/reuse-tool)
* publish images on Docker Hub?
* open CHANGELOG.md

## v0.2 - i18n first installable at Radio Campus Grenoble

* library explore and upload: add (save who did it)/edit/delete tracks
* add an increasing delay when restarting containers
* use container probe https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#container-probes
* install.sh: add an HTTP proxy that handles certificates and maybe Icecast
    cf. https://blog.hofstede.it/production-grade-container-deployment-with-podman-quadlets/ it could also be linked in doc to motivate quadlets
* "radiotomate update" as an init container.
* could the same install.sh be used to update ?
* i18n and user.lang - start with PyBabel, over a messagesDB format that is somewhat versioned by itself (at least, have a message modification date)
* CLI i18n !
* user's own page : change language or password
* auto-DJ's artist distance
* jingles time constraints (date/time min/max) by crontabs
* check all sound uploads have a spinner, currently big uploads are not visible
* add a link to sounds in "Edit cart" page
* try again crossfading, see https://www.liquidsoap.info/doc-dev/crossfade.html#custom-crossfades
* save beets id in MetadataLog.cart_id, test what happens when deleting sounds/carts that were logged
* containers auto-update, cf. https://github.com/TheQuantumPhysicist/example-podman-quadlet?tab=readme-ov-file#auto-updating-containers
* .liq logs to file if environment variable is set - how to log rotate ?
* carts list: instead of "Timed" show the schedule (full cron if advanced, or "Wed. 14:03" if simple)

## v0.3 - could be 1.0

* queue cleaner:
   - loop on each queue's requests.
   - if the request is not playing and have been pushed more than XX minutes ago, delete it.
   - that delay should be configurable, and be set to misfire_grace_time too
* calendar view of cart list
* carts page : filter and sort
* settings page, for admins
  - show radiotomate's version on that page, too
  - editable INTERFACE_NAME
  - set queue cleaner delay
  - set artist distance


## Next

_Sorted by priority. Releases are not planned yet._

* new queue: overlay (that would play mixed with the current one, could allow top horaires)
* keyboard shortcuts
* codeberg mirror
* sounds list: show forecasted playtime
* explore/export metadata_log from the interface
* AsyncScheduler.task_defaults have not been released yet, apparently
* is-warning on carts that have no upcomping sound (call ``next``)
* support cronTrigger start_time/end_time
* how to give an accessible equivalent to td.is-danger in #cartsrows ?
* RSS cart: auto-download from RSS
* add something from beets in a cart
* "force-push cart now" button
* automatic periodic DB exports + support restore
* upgrade htmx (cf. https://four.htmx.org/)
* audit log: trace what everybody does during preload_attributes
* automatic removal of old sounds in playlist carts?

## Ideas

* append URL when source is external stream
* adjustable gain for stream relay (store in url ?)
* enabling non-programmer translators: enable on user side a "translator" mode, that makes everything content-editable and wraps messages in span that gives the msgId so the browser can store all translations, and the ones it edited. Then add a button "export new translations" so send a "patch".
* regular ANALYZE https://sqlite.org/lang_analyze.html
* static analysis with Bandit https://bandit.readthedocs.io/en/latest/
* set LS'"visible" source IDs from YAML values
* when playing an AUDIO element, pause any other on the page
* live validator-explanation, create a cron descriptor - or maybe just show the date-time of the next 2 potential occurrences ?
* live-forcing metadata ? cf. https://www.liquidsoap.info/doc-dev/harbor_http.html#set-metadata
* progress meter: https://htmx.org/examples/file-upload/
* modula bulma ? https://bulma.io/documentation/start/modular/
* check https://apscheduler.readthedocs.io/en/3.x/userguide.html#missed-job-executions-and-coalescing
* switch dark/light mode from front
* put https://github.com/fiduswriter/Simple-DataTables#cdn https://fiduswriter.github.io/simple-datatables/documentation/Getting-Started#install
* cartfolders : how to check that a file will not be readable by LS ? a dedicated LS script ?
* /logout?user_id=... to invalid all users' sessions, for admins
* sessions view: close session, attributes should match browser/ip/time
* Live assist tab, with pads
* Live stream from browser ? no butt needed !
* Wordpress SSO - maybe https://wordpress.org/plugins/openid-connect-server/
* Importers from other systems:
  - Rivendell: cf. Rivendell2beets
  - libretime: let the user provide a tags filter

## Side-quests

### How could users extend the system ?

The first question is: which parts of the system would be worth extending?

The music library relies on [Beets](https://beets.io/), who already provides many
options and plug-ins.

We'll exclude the possibility of customizing the Liquidsoap script. First because of the
tight coupling between our script and a perticular Liquidsoap version. Years of practice
showed that each change to this couple must be carefully tested to ensure that our
scheduling logic remains very stable against the variety of surprises that sound streams
and streamers can create. Second because users that need to tweak the sound stream beyond
configuring outputs would probably be better with Azuracast. Azuracast had this approach
for a long time so they handle such customization better that we would do. Also we don't
envy the specific support requests that such customizations cause.

Maybe we could let the user implement, in Python:
 * hooks on meta-data events
 * additional interface tabs (implemented as Quart blueprints)
    - could be tried with the library explorer
    - but what about permissions ?
 * additional cart modes
    - could be tried to implement RCG's timed jingles
    - extending an Enum like CartMode is impossible, so we'd have to reimplement
      Enum-like methods and the SQLAlchemy mapping...

see also https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/#using-namespace-packages
we could put a radiotomate_plugins folder in $DATA_ROOT, with a sub-module per plug-in
category, then one module per plug-in. the main pakage itself could have this module
as an example.

Second question is: where should we draw the line between what's in Radiotomate and
what's left to extensions? How do we allow users to do their thing without falling
into a wordpress-like hell? For example, I don't think users should be allowed to change
the DB nor dealing with the scheduler.
