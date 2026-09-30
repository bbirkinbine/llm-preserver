# Reading quant names

A GGUF quant repo lists files and directories like `Q4_K_M`,
`UD-IQ4_XS`, `UD-Q4_K_XL`, `Q8_0` and `BF16`. There is no formal
standard behind these names. They are llama.cpp's quantization type
names, which every GGUF runtime (Ollama, LM Studio, llama.cpp itself)
inherited, plus extensions from individual publishers. This page
explains how to read them, so you can pick a pattern at the `pull`
prompt without guessing.

The tool does not interpret these names. It shows filenames and
sizes as the hub reports them, and you choose. (A future listing
annotation is queued in `TODO.md` as the "file-kind dictionary".)

## Reading a name left to right

Take `UD-Q4_K_XL`:

| Part | Meaning |
| --- | --- |
| `UD-` | Publisher prefix: Unsloth Dynamic (see below). Not a llama.cpp type. |
| `Q` / `IQ` | The family: `Q…_K` is a k-quant, `IQ` an i-quant, bare `Q4_0` a legacy quant. |
| `4` | Nominal bits per weight for most tensors. |
| `K` | Marks a k-quant. |
| `XL` / `M` / `S` / `XS` | The mix: how many of the sensitive tensors get extra bits. |

The number is only a nominal figure. Every family spends some extra bits on
scales, and most mixes keep a few tensors at higher precision, so a
"4-bit" file averages somewhat more than 4 bits per weight. **File size
is the honest number**: it is roughly what the model needs in RAM or
VRAM, plus headroom for context.

## The families

### Legacy quants: `Q4_0`, `Q4_1`, `Q5_0`, `Q8_0`

The original llama.cpp formats. Weights are split into blocks of 32,
each with one scale (`_0`) or a scale plus an offset (`_1`). They are
simple and fast everywhere. At 4 and 5 bits they have been superseded
by k-quants and i-quants of the same size. `Q8_0` is still widely
published because it is near-lossless: in llama.cpp's own reference
table it costs +0.0026 perplexity on Llama-3-8B, against +0.1754 for
`Q4_K_M`.

### K-quants: `Q2_K` through `Q6_K`, with `_S`, `_M`, `_L`

Added to llama.cpp in June 2023 (PR #1684). They group blocks into
256-weight *super-blocks* whose block scales are themselves quantized.
That packs scale information more efficiently than the legacy formats
and lowers the error at each size.

The suffix names a **mix**, not a different format. As PR #1684 defines
them:

- `Q4_K_S` uses the 4-bit k-quant for every tensor.
- `Q4_K_M` uses 6-bit `Q6_K` for half of two sensitive tensor kinds
  (attention value and feed-forward output), and 4-bit for the rest.
- `Q3_K_L` bumps the same sensitive tensors further than `Q3_K_M`.

So `_M` is `_S` with the most error-prone tensors kept at higher
precision. `Q4_K_M` became the common default: it is what most
runtimes, Ollama included, pick when they choose for you.

**Speed:** k-quants are the safe choice on every backend: CPU, CUDA,
and Apple Silicon (Metal). Their decode math is simple arithmetic on
the block scales.

### I-quants: `IQ1_S` through `IQ4_XS`, and `IQ4_NL`

Added to llama.cpp between January and February 2024: 2-bit in PR #4773,
`IQ4_NL` in #5590, and `IQ4_XS` in #5747. Instead of storing each
weight as a small integer, several are encoded together as an index
into a fixed table of good value patterns (a *codebook*, derived from
the E8 lattice for the low-bit types). `IQ4_NL` is *non-linear*: its
16 levels are unevenly spaced to match how weights are distributed.

I-quants are designed around an **importance matrix** (imatrix). This
comes from running the model over calibration text and recording which
weights matter most, so the quantizer spends its precision there
(PR #4861). That is the `imatrix` file many quant repos publish. The
`XXS`/`XS`/`S`/`M` suffixes are size tiers, smallest first. Examples
from llama.cpp's list: `IQ2_XXS` 2.06, `IQ3_XXS` 3.06, `IQ4_XS` 4.25
bits per weight.

**Quality:** at the same size an i-quant usually loses less than a
k-quant. The gap is largest at 1-3 bits, which is why the smallest
files in a repo are almost always i-quants. At 4 bits the two are
close.

**Speed:** decoding means looking values up in a table, and that costs
more on some hardware. When `IQ4_XS` was introduced, its author measured
it at or slightly above `Q4_0` speed on CUDA and on x86 and ARM CPUs,
but about 15% slower on Metal (53.9 vs 63.1 tokens/s). The same PR
called the CPU speed of `IQ3_S` "pretty bad" at the time. These are
2024 measurements, and kernels have improved since. The durable rule
of thumb: on a Mac GPU, or when CPU speed matters most, a k-quant of
similar size is the safer bet; on CUDA, i-quants cost little or
nothing.

### Unquantized: `F16`, `BF16`, `F32`

Full-precision GGUF conversions. Nothing is thrown away, so they are
the size of the original model (about 2 bytes per parameter for
16-bit). They matter for archiving because they are what you would
re-quantize *from* later. See [what-to-archive.md](what-to-archive.md)
→ "Deriving other quants later, offline".

## Publisher extensions

### `UD-` (Unsloth Dynamic)

Unsloth's own quantization recipe, applied on top of the llama.cpp
types. Their documentation describes it as adjusting the quantization
type of every possible layer, keeping important layers at higher bits
and unimportant ones lower, with an imatrix from their own calibration
dataset. The files are ordinary GGUFs that any llama.cpp-based runtime
loads; the prefix only tells you who chose the per-layer mix.

### `_XL` and other suffixes llama.cpp does not define

`llama-quantize` has no `_XL` type. `Q4_K_XL` is Unsloth's name, and
their documentation uses it without defining it. Its sizes are
consistent with a larger mix of the 4-bit k-quant, keeping more tensors
above 4 bits than `Q4_K_M` does, but that is an inference from the
files, not a published definition. Judge it by file size: in one GLM repo, `UD-Q4_K_XL` is
186.0 GiB against 146.1 GiB for `UD-IQ4_XS`. Other publishers invent
their own suffixes too; when a name is not in this page, read the
repo's model card.

## Choosing, in practice

1. **Start from what fits.** Pick the largest file that fits in your
   RAM or VRAM with room left for context.
2. **Among files of similar size:** on a Mac GPU or CPU-only, prefer
   the k-quant; on CUDA, either is fine, and the i-quant may give you
   slightly better quality for the bytes.
3. **Below 4 bits, prefer i-quants.** That is where they help most.
4. **If unsure, `Q4_K_M`** (or the publisher's `UD-Q4_K_XL`) is the
   widely used middle ground.
5. **For long-term archiving**, also consider the repo's
   `BF16`/`F16` and `imatrix` files: they let you make any of the
   above later, offline.

## Sources

Checked 2026-09-30.

- `llama-quantize --help`, llama.cpp build 10360 (`48d22e295`): the
  list of types, the bits-per-weight figures, and the Llama-3-8B size
  and perplexity table.
- llama.cpp pull requests (github.com/ggml-org/llama.cpp):
  #1684 "k-quants" (merged 2023-06-05; super-blocks and the `_S`/`_M`/`_L`
  mixes), #4773 "SOTA 2-bit quants" (2024-01-08; E8-lattice codebook),
  #4861 "Importance Matrix calculation" (2024-01-12), #5590 "IQ4_NL"
  (2024-02-21), #5747 "IQ4_XS: a 4.25 bpw quantization" (2024-02-27;
  the per-backend speed figures).
- Unsloth, "Unsloth Dynamic 3.0 GGUFs"
  (unsloth.ai/docs/basics/dynamic-3.0-ggufs): per-layer
  quantization-type adjustment and the imatrix calibration dataset. It
  does not define `XL`.
