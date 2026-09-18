/** Web MIDI — filet si la lib DOM du TS local ne les expose pas encore. */

interface MIDIOptions {
  sysex?: boolean;
  software?: boolean;
}

interface MIDIConnectionEvent extends Event {
  readonly port: MIDIPort;
}

interface MIDIMessageEvent extends Event {
  readonly data: Uint8Array;
}

interface MIDIPort extends EventTarget {
  readonly id: string;
  readonly manufacturer?: string;
  readonly name?: string;
  readonly type: "input" | "output";
  readonly state: "connected" | "disconnected";
  readonly connection: "open" | "closed" | "pending";
  open(): Promise<MIDIPort>;
  close(): Promise<MIDIPort>;
}

interface MIDIInput extends MIDIPort {
  onmidimessage: ((this: MIDIInput, ev: MIDIMessageEvent) => void) | null;
}

interface MIDIOutput extends MIDIPort {
  send(data: number[] | Uint8Array, timestamp?: number): void;
}

type MIDIInputMap = Map<string, MIDIInput>;
type MIDIOutputMap = Map<string, MIDIOutput>;

interface MIDIAccess extends EventTarget {
  readonly inputs: MIDIInputMap;
  readonly outputs: MIDIOutputMap;
  readonly sysexEnabled: boolean;
  onstatechange: ((this: MIDIAccess, ev: MIDIConnectionEvent) => void) | null;
}

interface Navigator {
  requestMIDIAccess(options?: MIDIOptions): Promise<MIDIAccess>;
}
