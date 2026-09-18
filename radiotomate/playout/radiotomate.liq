# Radiotomate's Liquidsoap script
# --------------------------
# This file is part of Radiotomate - Copyright (C) 2025 Martin Kirchgessner

# we have a few thread.run(fast=false so let's raise the number of fast queues
settings.scheduler.fast_queues := 5

settings.autocue.amplify_aliases := ["replaygain_track_gain", "rg_track_gain"]

# uncomment to set liquidsoap to "debug"
# settings.log.level := 5

# can be useful to debug, too
# server.telnet()

SCHEDULER_URL = "http://127.0.0.1:6822"

INPUT_PORT = 6800
API_PORT = 6833
BIND_ADDRS = ["0.0.0.0"]

BLANK_MAX_DURATION = 1.
BLANK_THRESHOLD = -45.

############################ CONFIGURATION LOADING #############################

path_to_config = environment.get("RTCONFIG")
if path_to_config == "" then
  print("Please set RTCONFIG to the path of the config file")
  shutdown(code=1)
end

if environment.get("AUTORELOAD") != "" then
  file.watch(liquidsoap.script.path, restart)
end

let yaml.parse (conf:{
  playout_process_config:{
    input_name:string,
    input_min_buffer:float,
    input_max_buffer:float,
    token:string,
    start_sound:string,
    log_path:string?,
    outputs: [{
      driver:string,
      device:string?,
      encoder:string?,
      quality:float?,
      host:string?,
      port:float?,
      mount:string?,
      password:string?,
    }]?
  },
  metadata_log:{
    extra_fields:[string],
  },
}) = file.contents(path_to_config)

MD_FIELDS = [
  ('initial_uri', 'keep'),
  ('source_url', 'keep'),
  ('source', 'keep'),
  ('on_air', 'keep'),
  ('artist', 'keep'),
  ('title', 'keep'),
  ('album', 'keep'),
  ('radiotomate_sound_id', 'keep'),
  ... list.map(fun (f) -> (f, 'keep'), conf.metadata_log.extra_fields ?? [])
]

settings.harbor.bind_addrs := BIND_ADDRS

if null.defined(conf.playout_process_config.log_path)
then
  log_path = null.get(conf.playout_process_config.log_path)
  settings.log.file.set(true)
  settings.log.file.path.set(log_path)
end


################################# TOOLING #####################################

def crash(message, code=1)
  print(message)
  if settings.log.file() then
    file.write(data=message, append=true, settings.log.file.path())
  end
  shutdown(code=code)
end

def post_to_radiotomate(endpoint, data)
  res = http.post("#{SCHEDULER_URL}#{endpoint}",
    headers=[
      ("Content-Type", "application/json; charset=UTF-8"),
      ("X-Auth-Token", conf.playout_process_config.token),
    ],
    data=json.stringify(data)
  )
  if res.status_code != 200
  then
    log(label="Warning", "Error while posting #{endpoint} to webapp: #{res} #{res.status_code} #{res.status_message}")
  end
  res
end


def http_log(req, res, next)
  # would be nice to have the request's host/IP - also errors in this function will block the response silently !
  started_at = time()
  next(req, res)
  spent = time() - started_at
  if req.method != 'GET' or (req.path != '/version' and req.path != '/health') then
    log(label="http", "#{req.method} #{req.path} #{res.status_code.current()} #{spent}")
  end
end
harbor.http.middleware.register(http_log)

def check_token(req, res, next)
  if req.method == "GET" and req.path == "/health" then
    next(req, res)
  elsif req.headers["x-auth-token"] != conf.playout_process_config.token then
    res.status_code(401)
  else
    next(req, res)
  end
end
harbor.http.middleware.register(check_token)


############################### MUSIC ####################################

autodj_queue = request.queue(id="autodj_queue")

autodj = blank.eat(id="autodj",
  start_blank=true,
  max_blank=BLANK_MAX_DURATION,
  threshold=BLANK_THRESHOLD,
  stereo( # If there's a mono file
    autodj_queue
  )
)

# this leaves 3 seconds for the scheduler process to push the first jingle,
# then should push the first music
autodj_queue.push(request.create(conf.playout_process_config.start_sound))

############################### JINGLES ########################################

jingles_queue = request.queue(id="jingles_queue")

jingles = blank.eat(id="jingles",
  start_blank=true,
  max_blank=BLANK_MAX_DURATION,
  threshold=BLANK_THRESHOLD,
  stereo( # If there's a mono file
    jingles_queue
  )
)

################################ EXTERNAL RELAY ################################

relay = input.http(id="relay", "https://live.campusgrenoble.org/rcg112",
  start=false, max_buffer=30., self_sync=false)


################################ LIVE SHOWS ####################################

# stream_auth_function ensures incoming streams are authenticated against Radiotomate's users directory
def stream_auth_function(login) =
  res = post_to_radiotomate("/can_stream", login)
  if res.status_code == 200 then
    log("Access granted to #{login.user}")
    true
  else
    log("Access denied to #{login.user}")
    false
  end
end

stream = input.harbor(
  id="stream",
  auth=stream_auth_function,
  buffer=conf.playout_process_config.input_min_buffer,
  max=conf.playout_process_config.input_max_buffer,
  port=INPUT_PORT,
  conf.playout_process_config.input_name
)

########################### CARTS QUEUE ################################

carts_queue = request.queue(id="carts_queue")

# sometimes recorded shows forget a blank in the end - make it at most 10 seconds
carts = blank.eat(id="carts",
  start_blank=true,
  max_blank=BLANK_MAX_DURATION,
  threshold=BLANK_THRESHOLD,
  stereo( # If there's a mono file
    carts_queue
  )
)

########################### COMBINING SOURCES ##################################

current_programs_source = ref("starting")
current_source_id = ref("starting")

# a 3-seconds crossfade operator that skips the cutted track.
# Skipping avoids getting back in the middle of a music track after a cart,
# but it requires a weird implementation. See https://github.com/savonet/liquidsoap/issues/1548
# also transitions are currently broken in LS 2.3 :/   https://github.com/savonet/liquidsoap/issues/4179
def crossfade_3s(a,b)
  # faded = add(normalize=false, [
  #   fade.in(duration=3., type="exp", b),
  #   amplify(mkfade(duration=3., start=1., stop=0., type="exp", a) ,a)
  # ])
  # thread.run(delay=10., { source.skip(a) })

  current_programs_source := source.id(b)
  current_source_id := current_programs_source()

  # if an input stream is on air, stop downloading from the relay
  if current_source_id() == "stream" then
    relay.stop()
  end

  # faded
  b
end


# The final `blank()` is only here so Liquidsoap will consider `programs` as a safe source.
programs = fallback([stream,   relay,     carts,        autodj,        blank()],
  transitions=[crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s],
  track_sensitive=false,
  replay_metadata=false,
  id="programs")

def update_current_source_id(_, b)
  current_source_id := source.id(b)
  if current_source_id() == "programs" then
    current_source_id := current_programs_source()
  end
  b
end

programs_and_jingles = fallback([jingles, programs],
  transitions=[update_current_source_id, update_current_source_id],
  id="programs_and_jingles",
  replay_metadata=false
)


settings.lufs.integrated_metadata := "rg_track_gain"
radio = normalize_track_gain(programs_and_jingles)


def post_to_log(md)
  log(label="replaygain", md["rg_track_gain"])
end

radio.on_metadata(synchronous=false, post_to_log)



############################ ♫ OUTPUT ♫ ########################################

def get_encoder(family, quality) = 
  quality_i = int_of_float(quality)
  if family == 'mp3cbr' then
    %mp3.cbr(bitrate=quality_i, samplerate=44100, stereo=true)
  elsif family == 'mp3vbr' then
    %mp3.vbr(quality=quality_i)
  elsif family == 'flac' then
    %ogg(
      %flac(samplerate=44100, channels=2, compression=quality_i, bits_per_sample=16)
    )
  elsif family == 'vorbiscbr' then
    %vorbis.cbr(samplerate=44100, channels=2, bitrate=quality_i)
  elsif family == 'vorbisvbr' then
    %vorbis(samplerate=44100, channels=2, quality=quality)
  elsif family == 'wav' then
    %wav()
  else
    intro = "skipping output configuration block, unsupported value for 'encoder': "
    outro = " (should be one of: mp3cbr, mp3vbr, flac, vorbiscbr, vorbisvbr, wav)"
    log.important(intro ^ family ^ outro)
    null
  end
end

def output_build_pulse(config) =
  output.pulseaudio(radio, device=config.device)
end

def output_build_alsa(config) =
  output.alsa(radio, device=null.get(config.device, default="default"))
end

def output_build_icecast(config) =
  if not null.defined(config.encoder) or
    not null.defined(config.quality) or
    not null.defined(config.host) or
    not null.defined(config.port) or
    not null.defined(config.mount)
    then
    log.important("skipping icecast configuration block: it needs to contain at least encoder, quality, host, port and mount")
    null
  else
    enc = get_encoder(null.get(config.encoder, default=""), null.get(config.quality, default=1.))
    if null.defined(enc) then
      hostname = null.get(config.host)
      port = int_of_float(null.get(config.port))
      mount = null.get(config.mount)
      password = null.get(config.password, default="")
      output.icecast(
        host=hostname,
        port=port,
        mount=mount,
        password=password,
        null.get(enc),
        radio
      )
    else
      null
    end
  end
end

def outputs_builder(config) =
  # FIXME these two lines are a workaround while waiting for https://github.com/savonet/liquidsoap/issues/4285
  opt = {str="", flo=1.}
  config = true ? config : {driver="", device=opt?.str, encoder=opt?.str, quality=opt?.flo, host=opt?.str, port=opt?.flo, mount=opt?.str, password=opt?.str}

  if config.driver == "pulseaudio" then
    output_build_pulse(config)
  elsif config.driver == "alsa" then
    output_build_alsa(config)
  elsif config.driver == "icecast" then
    output_build_icecast(config)
  else
    intro = "skipping output configuration block, unsupported value for 'driver': "
    outro = " (should be 'alsa', 'pulseaudio' or 'icecast')"
    log.important(intro ^ config.driver ^ outro)
    null
  end
end

outputs = null.get(conf.playout_process_config.outputs, default=[])
if list.length(outputs) == 0 then
  crash("\n\n\n\n\n !!!! No output has been set-up !!!!\n\n\n\n\n" ^
    "Edit the file #{path_to_config} to define outputs (remove all # before the example block that fits your needs)\n\n\n")
end

outputs = list.filter_map(outputs_builder, outputs)
outputs_nb = string.of_int(list.length(outputs))
log.important("started #{outputs_nb} outputs")

#################### PLAYOUT LOG TO SCHEDULER SERVICE ##########################

# raw metadata can weight a few megabytes so we'll filter_md only once
current_md = ref(json())

# keep a pre-computed version of queued requests' metadata
next_jingle = ref({title="", artist="", rid=-1, initial_uri=""})
next_autodj = ref({title="", artist="", rid=-1, initial_uri=""})
next_cart = ref({title="", artist="", rid=-1, initial_uri=""})

def filter_md(md)
  let data = json()
  list.iter(fun (v) -> if MD_FIELDS[fst(v)] == 'keep' then data.add(fst(v),snd(v)) end, md)
  data
end

#### Metadata log

def post_to_log(md)
  data = filter_md(md)
  data.add("on_air", time.string('%Y-%m-%d %H:%M:%S'))
  current_md := data
  data.add("source", current_source_id())
  _ = post_to_radiotomate("/metadata_log", data)
end

radio.on_metadata(synchronous=false, post_to_log)

#### Regular update of the "live" panel

def fill_next(data, field, q, cached)
  cached = true ? cached : ref({title="", artist="", rid=-1, initial_uri=""}) # FIXME this is a workaround while waiting for https://github.com/savonet/liquidsoap/issues/4285
  try
    current_maybe = q.current()
    r = if null.defined(current_maybe) then
      # this request is playing, or ready to play but its queue is not activated yet.
      null.get(current_maybe)
    else
      list.find(fun (r) -> request.status(r) != "playing", q.queue())
    end
    status = request.status(r)
    rid = request.id(r)

    if rid == cached().rid then
      log.debug("#{field} return cached RID#{rid}, #{q.length()} items enqueued")
      data.add(field, cached())
    elsif status == "ready" then
      md = request.metadata(r)
      cached := {title=md["title"], artist=md["artist"], rid=rid, initial_uri=request.uri(r)}
      log.debug("#{field} update cache with RID#{rid}, #{q.length()} items enqueued")
      data.add(field, cached())
    else
      log.debug("#{field} unexpected case for RID#{rid}, #{q.length()} items enqueued")
      data.add(field, {title="", artist="", rid=rid, initial_uri=request.uri(r)})
    end

  catch err : [error.not_found] do
    # when queue is empty
    log.debug("#{field} is empty")
    data.add(field, {rid=-1})
    ignore(err)
  end
end

def post_to_live()
  data = current_md()
  data.add("source", current_source_id())
  data.add("remaining", string.float(radio.remaining()))
  data.add("elapsed", string.float(radio.elapsed()))
  data.add("time", time.string('%Y-%m-%dT%H:%M:%S%z'))

  fill_next(data, "next_jingle", jingles_queue, next_jingle)
  fill_next(data, "next_autodj", autodj_queue, next_autodj)
  fill_next(data, "next_cart", carts_queue, next_cart)

  _ = post_to_radiotomate("/live", data)
end

thread.run(every=1., delay=1., fast=false, post_to_live)

######################## SCHEDULER'S ENDPOINTS ##############################

#### Version

def version_GET(_, res) =
  res.json({version=liquidsoap.version})
end

harbor.http.register(port=API_PORT, method="GET", "/version", version_GET)

def health_GET(_, res) =
  res.json({status="live"})
end

harbor.http.register(port=API_PORT, method="GET", "/health", health_GET)

#### Skip / max duration / stop relay

def live_DELETE(req, res) =
  radiotomate_sound_id = req.query["radiotomate_sound_id"]
  if string.length(req.query["relay"]) > 0 then
    relay.stop()
    res.status_code(200)
  elsif string.length(radiotomate_sound_id) > 0 then
    # this implements carts' max duration - when we don't need to skip, return 204 No Content
    res.status_code(204)
    playing_maybe = carts_queue.current()
    if null.defined(playing_maybe) then
      playing = null.get(playing_maybe)
      md = request.metadata(playing)
      if md["radiotomate_sound_id"] == radiotomate_sound_id and
        carts_queue.remaining() > 1.
        then
          rid = request.id(playing)
          log("Forcing max duration for sound_id##{radiotomate_sound_id} (was RID#{rid})")
          carts_queue.skip()
          res.status_code(200)
      end
    end
  else
    radio.skip()
    res.status_code(200)
  end
end

harbor.http.register(port=API_PORT, method="DELETE", "/live", live_DELETE)

#### push content

def queue_POST(req, res) =
  queue_name = req.query["queue"]
  let json.parse (posted:{
    path: string,
    artist: string?,
    title: string?,
    radiotomate_sound_id: int?,
    rg_track_gain: float?,
  }) = req.body()

  pushed = time()
  annotated = "annotate:pushed=\"#{pushed}\"" ^
    if null.defined(posted.radiotomate_sound_id) then
      ",radiotomate_sound_id=\"#{null.get(posted.radiotomate_sound_id)}\""
    else
      ""
    end
    ^
    if null.defined(posted.title) then
      ",title=\"#{null.get(posted.title)}\""
    else
      ""
    end
    ^
    if null.defined(posted.artist) then
      ",artist=\"#{null.get(posted.artist)}\""
    else
      ""
    end
    ^
    if null.defined(posted.rg_track_gain) then
      ",rg_track_gain=\"#{null.get(posted.rg_track_gain)}\""
    else
      ""
    end
    ^ ":" ^ posted.path

  try
    q =
      if queue_name == "autodj" then
        autodj_queue
      elsif queue_name == "jingles" then
        jingles_queue
      elsif queue_name == "carts" then
        carts_queue
      else
        error.raise(error.not_found, "unknown queue #{queue_name}")
      end

    r = request.create(annotated)
    if (request.resolve(r, content_type=q)) then
      q.push(r)
      res.status_code(200)
      res.json({ok=request.id(r)})
    else
      res.status_code(400)
      message = request.log(r)
      res.json({error="cannot resolve #{path} - #{message}"})
    end
  catch err : [error.not_found] do
    res.status_code(400)
    res.json({error=err.message})
  end
end

harbor.http.register(port=API_PORT, method="POST", "/queue/:queue", queue_POST)


def _queue_clean_result(q, max_age)
  now = time()
  removed = ref(0)
  kept = ref(0)
  playing = ref(-1)
  current_maybe = q.current()
  if null.defined(current_maybe) then
    playing := request.id(null.get(current_maybe))
  end
  list.iter(fun (r) -> begin
    rid = request.id(r)
    if rid == playing() then
      kept := kept() + 1
    else
      md = request.metadata(r)
      pushed_s = md["pushed"]
      if string.length(pushed_s) == 0 then
        kept := kept() + 1
      else
        try
          age = now - float_of_string(pushed_s)
          if age > max_age then
            q.remove_request_id(rid)
            removed := removed() + 1
          else
            kept := kept() + 1
          end
        catch err do
          kept := kept() + 1
          ignore(err)
        end
      end
    end
  end, q.queue())
  {removed=removed(), kept=kept()}
end


def queue_clean_POST(req, res) =
  max_age = ref(600.)
  try
    let json.parse (posted:{max_age_seconds: float?}) = req.body()
    if null.defined(posted.max_age_seconds) then
      max_age := null.get(posted.max_age_seconds)
    end
  catch err do
    ignore(err)
  end
  age = max_age()
  clamped =
    if age < 30. then
      30.
    elsif age > 7200. then
      7200.
    else
      age
    end
  res.status_code(200)
  res.json({
    autodj=_queue_clean_result(autodj_queue, clamped),
    jingles=_queue_clean_result(jingles_queue, clamped),
    carts=_queue_clean_result(carts_queue, clamped)
  })
end

harbor.http.register(port=API_PORT, method="POST", "/queue/clean", queue_clean_POST)


def relay_POST(req, res) =
  query_url = req.query["url"]
  if string.length(query_url) > 0 then
    relay.set_url(query_url)
    relay.start()
    res.status_code(200)
    res.json({ok=relay.status()})
  else
    res.status_code(400)
    res.json({error="'url' query parameter is empty"})
  end
end

harbor.http.register(port=API_PORT, method="POST", "/relay", relay_POST)
