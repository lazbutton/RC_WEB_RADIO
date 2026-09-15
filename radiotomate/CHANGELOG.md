# Radiotomate's change log

## (upcoming) 0.1

This first release settles Radiotomate's architecture by implementing the most
important features:

* Auto-DJ as non-overlapping time period that select via a (weighted) Beets filter(s)
* Carts as a collection of sounds picked in order or randomly
* "simple" mode for timed carts: pick a weekday and a HH:MM
* "advanced" mode: cron formulas for timed carts
* Relay external streams
* External stream input
* Automated switch to the first available source among external/relay/carts/autoDJ
* "Live" page displaying what's currently playing
* Log metadata and relay it to other HTTP endpoints
* Configurable outputs, to Icecast or ALSA
* Apply replay gain to music, carts and jingles
* Basic user account creation and access rights
* A fairly complete automated test suite that also checks the interface remains
  accessible to screen readers
* A command-line interface for installation, updates and user management

All of that implemented in light, asynchronous web-apps, storing to SQlite and rendering
server-side, using HTMX when we need some dynamic rendering.

It also comes with an automated pod creator:

* a playout container based on Liquidsoap's
* webapps container
* hardcodes that interface is on `:6811`, scheduler on `:6822`, playout on `:6833` and playout input on `:6800` - we leave ports choice to the host, but:
* transparent paths bindings in containers, so application's data has the same path in and outside the contaniner
* provide `beets` and `radiotomate` commands working on the usual $PATH outside the container
* a parameter to configure pulseaudio bind or not
* ... without ever being root

And minor/less visible things:

* automated developer environment installation
* carts' maximum duration
* integrate beets 2.5, with drop2beets
* ensure external.stop when switching to live
* configurable logging to files
* Liquidsoap script crash when no output is configured, and suggests to check the config
* save who uploaded a sound/music
* transform hx-redirect into redirect(), when it's a POST, and handling 4XX messages as alerts.
* binary flag for autoreload, with LS 2.3.4
* ability to push cart on the autodj queue (for promo spots)
* default start_sound copied in data_root
