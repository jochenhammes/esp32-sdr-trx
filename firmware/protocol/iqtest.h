/*
 * RESEARCH ONLY (IQTEST=1 builds, never part of a release): control ops to test the DAC playback engine at 0x60033D64
 * (docs/research/PLAN-IQ-TX.md, Phase A). All ops are ordinary requests/responses; the host script is scripts/iqtest.py.
 *
 * Sequence: TX_OP_LO, IQ_OP_BEGIN (receiver released, PLL tuned, PHY transmit test mode), IQ_OP_KEY (carrier chain on, bank granted),
 * IQ_OP_ROT_* + IQ_OP_FILL (samples into bank 2), IQ_OP_PLAY (re-trigger the engine for IQ_OP_MS milliseconds), ..., IQ_OP_END.
 * If no IQ op arrives for IQ_IDLE_MS the chip ends the session by itself.
 */
#pragma once

#define IQ_OP_BEGIN 50 /* prepare like TX_OP_BEGIN (needs TX_OP_LO); arg bit 0: leave out txcal_debuge_mode(); the response value is the PLL word */
#define IQ_OP_KEY 51   /* arg: bits 7:0 gain code for start_tx_tone_step, bits 15:8 value for 0x600C101C (bank grant) written after keying; value: its readback */
#define IQ_OP_GAIN 52  /* arg: gain code (bits 17:10 of 0x60006040) */
#define IQ_OP_ROT_COS 53 /* arg: cos of the phase step per sample, signed Q30 */
#define IQ_OP_ROT_SIN 54 /* arg: sin of the phase step per sample, signed Q30 */
#define IQ_OP_FILL 55  /* arg: bits 9:0 amplitude (0..511), bits 19:16 mode (IQ_MODE_*): fills the whole of bank 2 (16384 words) */
#define IQ_OP_MS 56    /* arg: duration of the next IQ_OP_PLAY in ms (1 .. 3000) */
#define IQ_OP_PLAY 57  /* arg: bits written to 0x60033D64 (count-1 in 13:0, rate bit 15, hold bit 19, ...; bit 31 and 18 are handled here);
                          value: bits 15:0 triggers completed, bits 31:16 polls that timed out */
#define IQ_OP_END 58   /* stop, un-key, set the receiver up again */
#define IQ_OP_ADDR 59  /* arg: address for IQ_OP_POKE / IQ_OP_PEEK (0x60000000..0x600FFFFF, 32-bit aligned) */
#define IQ_OP_POKE 60  /* arg: 32-bit value written to the address */
#define IQ_OP_PEEK 61  /* value: the 32-bit word at the address (addresses also in capture banks 0..2, 0x3FCB0000..0x3FCDFFFC) */
#define IQ_OP_KEY_RAW 65 /* like KEY2 but with the receiver left configured (no BEGIN, no PBUS release, no PLL change); IQ_OP_END restores it. arg as KEY2 */
#define IQ_OP_PWR 66     /* arg bit 0: trigger the engine (with the bits of the last IQ_OP_PLAY) around each reading. value: sum of 64 readings of the PHY's txtone_linear_pwr() (signed 16 bit each) */
#define IQ_OP_ANA_RD 67  /* arg: host << 24 | block << 8 | register (analog I2C block, e.g. 0x67; host 0 means 1); value: its content */
#define IQ_OP_ANA_WR 68  /* arg: host << 24 | value << 16 | block << 8 | register */
#define IQ_OP_PRE_G 69   /* arg: signed Q16 gain error of Q for IQ_OP_FILL: Q' = (1 + g) * Q + p * I */
#define IQ_OP_PRE_P 70   /* arg: signed Q16 I-to-Q leakage p (see IQ_OP_PRE_G) */
#define IQ_OP_ROT2_COS 71 /* second rotator for IQ_MODE_TWO, as IQ_OP_ROT_COS */
#define IQ_OP_ROT2_SIN 72
#define IQ_OP_STREAM_G 73   /* arg: pause between two buffers in 40 MHz samples, unsigned Q8 (added to the NCO phase at every buffer boundary) */
#define IQ_OP_STREAM_INC 74 /* arg: NCO phase increment per 40 MHz sample, 2^32 = 40 MHz (signed) */
#define IQ_OP_STREAM_INC2 75 /* arg: second increment; if not 0 odd buffers use it (a test that the content changes between buffers) */
#define IQ_OP_STREAM 76     /* arg: amplitude 1..255. E1: the engine plays at 40 Msps for IQ_OP_MS ms while the CPU writes the next buffer behind the reader. value: bits 15:0 buffers played, 31:16 late words (saturating) */
#define IQ_OP_BENCH 77     /* arg 0: cycles to write 16384 words from the lookup table (engine idle); 1: the same while the engine plays at 40 Msps; 2: the same at 80 Msps; 3: only the engine duration at 40 Msps (cycles) */
#define IQ_OP_STREAM_CHECK 78 /* after IQ_OP_STREAM with an exact increment: compares bank 2 with the ideal tone continuing from word 0; value: bits 15:0 mismatching words, 31:16 index of the first one (0xFFFF none) */
#define IQ_OP_WCHECK 79   /* arg 0: write 16384 words with the block writer (IQ_OP_STREAM_INC, amplitude 150) with the engine idle and check them; 1: the same with the engine playing at 40 Msps meanwhile; value as IQ_OP_STREAM_CHECK */
#define IQ_OP_GDMA 80      /* E2: copy bank 1 (tone from IQ_OP_STREAM_INC, amplitude 150) to bank 2 with the GDMA memory-to-memory channel; arg bit 0: engine playing at 40 Msps meanwhile, bit 1: burst mode. value: bits 15:0 words of bank 2 that differ from bank 1, 31:16 index of the first */
#define IQ_OP_GDMA_TIME 81 /* CPU cycles the last IQ_OP_GDMA copy took */
#define IQ_OP_PBUS_RD 63 /* arg: block << 4 | index; value: the analog bus register (9 bits) */
#define IQ_OP_PBUS_WR 64 /* arg: value << 8 | block << 4 | index */
#define IQ_OP_KEY2 62  /* like IQ_OP_KEY but with the PHY's phy_txtone_start(mhz, 0, power): arg bits 15:0 mhz, 23:16 power, 31:24 bank grant */

#define IQ_MODE_ROTATOR 0 /* I + jQ = A * w^n (complex tone; w from IQ_OP_ROT_*) */
#define IQ_MODE_REAL 1    /* I = Re(A * w^n), Q = 0 */
#define IQ_MODE_CONST 2   /* I = A, Q = 0 */
#define IQ_MODE_ZERO 3    /* all words 0 */
#define IQ_MODE_TWO 5     /* I + jQ = A/2 * (w1^n + w2^n): two complex tones (w1 from the ROT ops, w2 from the ROT2 ops) */
#define IQ_MODE_RAW 4     /* every word = the value given with IQ_OP_ROT_COS (raw 32-bit pattern) */

#define IQ_BANK2 0x3FCD0000u
#define IQ_WORDS 16384u
#define IQ_DAC_REG 0x60033D64u
#define IQ_BANK_SELECT_REG 0x600C101Cu
#define IQ_IDLE_MS 20000u
