// SPDX-License-Identifier: GPL-2.0-only
// SM 12.0 (sm_120a) TMA + mbarrier + Warp-Specialized (128x256x64) Dual QMMA.SF
// W8A16 (E4M3 x E4M3) & W4A16 (E4M3 x E2M1) GEMM benchmark and reference kernels.
//
// Splits BF16 activations per 32-element K-block as X = s * (H + L / 16) with
// power-of-2 UE8M0 scale s (mapping max|X| into [128, 256)), then accumulates both
// H*W (scale s) and L*W (scale s/16 = sH - 4) into the SAME FP32 accumulator via
// full-rate QMMA.SF.16832 (2048 raw FP8 FLOP/SM/clk -> 1024 eff W8A16/W4A16 FLOP/SM/clk).
//
// Build (CUDA 13.x+, Blackwell sm_120a):
//   Linux:   nvcc -O3 -std=c++17 -gencode arch=compute_120a,code=sm_120a \
//            software/dual_qmma_tma_ws.cu -o dual_qmma_tma_ws -lcuda -lcublas -lnvidia-ml
//   Windows: nvcc -O3 -std=c++17 -gencode arch=compute_120a,code=sm_120a \
//            software/dual_qmma_tma_ws.cu -o dual_qmma_tma_ws.exe -lcuda -lcublas -lnvml
// Run:
//   ./dual_qmma_tma_ws [--json out.json]

#include <cstdio>
#include <cstdint>
#include <cmath>
#include <cstring>
#include <vector>
#include <string>
#include <random>
#include <algorithm>
#include <thread>
#include <atomic>
#include <chrono>
#include <cuda.h>
#include <cuda_runtime.h>
#include <cuda_fp16.h>
#include <cuda_bf16.h>
#include <cuda_fp8.h>
#include <cublas_v2.h>
#include <nvml.h>

#define CHECK_CUDA(x) do { cudaError_t e = (x); if (e != cudaSuccess) { fprintf(stderr, "CUDA ERR %s at %d\n", cudaGetErrorString(e), __LINE__); exit(1); } } while(0)
#define CHECK_CU(x)   do { CUresult r = (x); if (r != CUDA_SUCCESS) { const char* s=nullptr; cuGetErrorName(r,&s); fprintf(stderr, "CU ERR %d (%s) at %d\n", (int)r, s?s:"?", __LINE__); exit(1); } } while(0)

__device__ __forceinline__ void mbar_init(uint32_t addr, uint32_t count) {
    asm volatile("mbarrier.init.shared::cta.b64 [%0], %1;\n" :: "r"(addr), "r"(count));
}
__device__ __forceinline__ void mbar_expect_tx(uint32_t addr, uint32_t tx_bytes) {
    asm volatile("mbarrier.arrive.expect_tx.shared::cta.b64 _, [%0], %1;\n" :: "r"(addr), "r"(tx_bytes));
}
__device__ __forceinline__ void mbar_arrive(uint32_t addr) {
    asm volatile("mbarrier.arrive.shared::cta.b64 _, [%0];\n" :: "r"(addr));
}
__device__ __forceinline__ void mbar_wait(uint32_t addr, uint32_t phase) {
    // Hardware-sleeping try_wait (10M-tick timeout) avoids spin-loop SMEM contention.
    asm volatile("{\n.reg .pred P1;\nLW:\nmbarrier.try_wait.parity.shared::cta.b64 P1, [%0], %1, 0x989680;\n@!P1 bra LW;\n}\n" :: "r"(addr), "r"(phase));
}
__device__ __forceinline__ void tma_load_2d(uint32_t smem_dst, const CUtensorMap* tmap, int32_t c0, int32_t c1, uint32_t mbar_addr) {
    asm volatile("cp.async.bulk.tensor.2d.shared::cta.global.tile.mbarrier::complete_tx::bytes [%0], [%1, {%2, %3}], [%4];\n"
                 :: "r"(smem_dst), "l"(tmap), "r"(c0), "r"(c1), "r"(mbar_addr) : "memory");
}
__device__ __forceinline__ void tma_load_1d(uint32_t smem_dst, const void* gmem_src, uint32_t bytes, uint32_t mbar_addr) {
    asm volatile("cp.async.bulk.shared::cta.global.mbarrier::complete_tx::bytes [%0], [%1], %2, [%3];\n"
                 :: "r"(smem_dst), "l"(gmem_src), "r"(bytes), "r"(mbar_addr) : "memory");
}
template<int N> __device__ __forceinline__ void warpgroup_reg_alloc()   { asm volatile("setmaxnreg.inc.sync.aligned.u32 %0;\n" :: "n"(N)); }
template<int N> __device__ __forceinline__ void warpgroup_reg_dealloc() { asm volatile("setmaxnreg.dec.sync.aligned.u32 %0;\n" :: "n"(N)); }
__device__ __forceinline__ void ldmatrix_x4(uint32_t& r0, uint32_t& r1, uint32_t& r2, uint32_t& r3, uint32_t smem_addr) {
    asm volatile("ldmatrix.sync.aligned.m8n8.x4.shared.b16 {%0,%1,%2,%3}, [%4];\n" : "=r"(r0),"=r"(r1),"=r"(r2),"=r"(r3) : "r"(smem_addr));
}
__device__ __forceinline__ int swz32(int row, int c16) { return c16 ^ ((row >> 2) & 1); }

// Vectorized pair hardware BF16 -> (H_e4m3x2, L_e4m3x2).
// Every finite E4M3 value (normal and subnormal, min 2^-9) embeds exactly in FP16.
__device__ __forceinline__ void split_f32x2_to_hl2(float x0, float x1, uint16_t& h_pair, uint16_t& l_pair) {
    asm volatile("cvt.rn.satfinite.e4m3x2.f32 %0, %1, %2;\n" : "=h"(h_pair) : "f"(x1), "f"(x0));
    uint32_t hf16_pair;
    asm volatile("cvt.rn.f16x2.e4m3x2 %0, %1;\n" : "=r"(hf16_pair) : "h"(h_pair));
    float2 hf = __half22float2(*reinterpret_cast<__half2*>(&hf16_pair));
    float r0 = 16.0f * (x0 - hf.x);
    float r1 = 16.0f * (x1 - hf.y);
    asm volatile("cvt.rn.satfinite.e4m3x2.f32 %0, %1, %2;\n" : "=h"(l_pair) : "f"(r1), "f"(r0));
}

// Split 8 consecutive BF16 values belonging to a 32-element block (4 threads/block).
__device__ __forceinline__ void split_8bf16_quad(uint4 raw, uint32_t& sH_out, uint2& h_out, uint2& l_out) {
    const __nv_bfloat162* b2 = reinterpret_cast<const __nv_bfloat162*>(&raw);
    float2 p0 = __bfloat1622float2(b2[0]);
    float2 p1 = __bfloat1622float2(b2[1]);
    float2 p2 = __bfloat1622float2(b2[2]);
    float2 p3 = __bfloat1622float2(b2[3]);

    float ax = fmaxf(fmaxf(fabsf(p0.x), fabsf(p0.y)), fmaxf(fabsf(p1.x), fabsf(p1.y)));
    ax = fmaxf(ax, fmaxf(fmaxf(fabsf(p2.x), fabsf(p2.y)), fmaxf(fabsf(p3.x), fabsf(p3.y))));
    ax = fmaxf(ax, __shfl_xor_sync(0xffffffffu, ax, 1));
    ax = fmaxf(ax, __shfl_xor_sync(0xffffffffu, ax, 2));

    uint32_t sH_byte;
    float inv_s;
    if (ax == 0.0f) {
        sH_byte = 127u;
        inv_s = 1.0f;
    } else {
        int emax = (int)((__float_as_uint(ax) >> 23) & 0xFFu);
        int sh = max(emax - 7, 4); // keep sH >= 4 so sL = sH - 4 never underflows
        sH_byte = (uint32_t)sh;
        inv_s = __uint_as_float((uint32_t)(254 - sh) << 23);
    }
    sH_out = sH_byte;

    uint16_t h0, h1, h2, h3, l0, l1, l2, l3;
    split_f32x2_to_hl2(p0.x * inv_s, p0.y * inv_s, h0, l0);
    split_f32x2_to_hl2(p1.x * inv_s, p1.y * inv_s, h1, l1);
    split_f32x2_to_hl2(p2.x * inv_s, p2.y * inv_s, h2, l2);
    split_f32x2_to_hl2(p3.x * inv_s, p3.y * inv_s, h3, l3);
    h_out = make_uint2((uint32_t)h0 | ((uint32_t)h1 << 16), (uint32_t)h2 | ((uint32_t)h3 << 16));
    l_out = make_uint2((uint32_t)l0 | ((uint32_t)l1 << 16), (uint32_t)l2 | ((uint32_t)l3 << 16));
}

// Standalone BF16 -> (H, L, sH) split prologue kernel (4 threads per 32-element block).
// Output: H[M, K] uint8, L[M, K] uint8, sH[K/32, M] uint8 (K-block outer).
__global__ void split_bf16_to_dual_fp8_kernel(
    const __nv_bfloat16* __restrict__ A,
    uint8_t* __restrict__ H,
    uint8_t* __restrict__ L,
    uint8_t* __restrict__ sH,
    int M, int K
) {
    int num_kb = K >> 5;
    int tid_g = blockIdx.x * blockDim.x + threadIdx.x;
    int blk_g = tid_g >> 2;
    int q = tid_g & 3;
    int total_blocks = M * num_kb;
    if (blk_g >= total_blocks) return;

    int row = blk_g / num_kb;
    int kb  = blk_g - row * num_kb;
    int elem_off = row * K + (kb << 5) + (q << 3);

    uint4 raw = *reinterpret_cast<const uint4*>(A + elem_off);
    uint32_t sH_byte; uint2 h8, l8;
    split_8bf16_quad(raw, sH_byte, h8, l8);

    *reinterpret_cast<uint2*>(H + elem_off) = h8;
    *reinterpret_cast<uint2*>(L + elem_off) = l8;
    if (q == 0) sH[(size_t)kb * M + row] = (uint8_t)sH_byte;
}

// Unified TMA-WS 128x256x64 QMMA.SF kernel.
//   USE_W4       = false: W8A16 (E4M3 x E4M3), W in GMEM as [N, K] uint8
//   USE_W4       = true : W4A16 (E4M3 x E2M1), W in GMEM as [N, K/2] uint8 (interleaved k, k+16 nibbles)
//   IS_DUAL      = false: Single-FP8 (H*W)
//   IS_DUAL      = true : Dual-FP8 (H*W + (L/16)*W into the SAME FP32 accumulator)
//   ONLINE_SPLIT = false: Reads pre-split H, L, sH via TMA
//   ONLINE_SPLIT = true : Reads raw BF16 A via TMA; producer warps 8..11 split asynchronously
template<bool USE_W4, bool IS_DUAL, bool ONLINE_SPLIT>
__global__ void __launch_bounds__(384, 1) gemm_qmma_sf_tma_ws(
    const __grid_constant__ CUtensorMap tmapA_H,
    const __grid_constant__ CUtensorMap tmapA_L,
    const __grid_constant__ CUtensorMap tmapW,
    const uint8_t* __restrict__ sH_gmem,
    const uint8_t* __restrict__ sW_gmem,
    float* __restrict__ C,
    int M, int N, int K
) {
    static constexpr int BM = 128, BN = 256, BK = 64, STAGES = 2;
    static constexpr int WN = 64, N_SUB = 8;
    static constexpr int STRIP_BYTES = 128 * 32;

    static constexpr int H_BYTES  = 2 * STRIP_BYTES;
    static constexpr int L_BYTES  = IS_DUAL ? (2 * STRIP_BYTES) : 0;
    static constexpr int W_BYTES  = USE_W4 ? (2 * STRIP_BYTES) : (4 * STRIP_BYTES);
    static constexpr int SH_BYTES = 256;
    static constexpr int SW_BYTES = 512;
    static constexpr int STAGE_BYTES = H_BYTES + L_BYTES + W_BYTES + SH_BYTES + SW_BYTES;
    static constexpr int ABF16_BYTES = ONLINE_SPLIT ? (128 * 64 * 2) : 0;

    extern __shared__ __align__(128) uint8_t smem_raw[];
    uint32_t smem_base = (uint32_t)__cvta_generic_to_shared(smem_raw);
    uint32_t smem_abf16 = smem_base + STAGES * STAGE_BYTES;
    uint32_t mbar_full_base  = smem_abf16 + ABF16_BYTES;
    uint32_t mbar_empty_base = mbar_full_base + STAGES * 8;
    uint32_t mbar_tma_a      = mbar_empty_base + STAGES * 8;

    int tid = threadIdx.x, warp_id = tid >> 5, lane = tid & 31;
    int grid_m = M / BM, grid_n = N / BN, bid = blockIdx.x;
    int grp_m = 8, tiles_per_grp = grp_m * grid_n;
    int grp_id = bid / tiles_per_grp, in_grp = bid % tiles_per_grp;
    int cur_grp_m = min(grp_m, grid_m - grp_id * grp_m);
    int bm = grp_id * grp_m + (in_grp % cur_grp_m), bn = in_grp / cur_grp_m;

    if (tid == 0) {
        #pragma unroll
        for (int s = 0; s < STAGES; ++s) {
            mbar_init(mbar_full_base  + s * 8, ONLINE_SPLIT ? 129u : 1u);
            mbar_init(mbar_empty_base + s * 8, 256u);
        }
        if constexpr (ONLINE_SPLIT) mbar_init(mbar_tma_a, 1u);
        asm volatile("fence.mbarrier_init.release.cluster;\n" ::);
    }
    __syncthreads();

    int num_tiles = K / BK;
    int m_base = bm * BM, n_base = bn * BN;

    if (warp_id >= 8) {
        warpgroup_reg_dealloc<40>();
        int pwid = warp_id - 8;
        int stage = 0;
        uint32_t empty_phase = 1;
        uint32_t tma_a_phase = 0;

        if constexpr (!ONLINE_SPLIT) {
            if (pwid == 0 && lane == 0) {
                for (int kt = 0; kt < num_tiles; ++kt) {
                    if (kt >= STAGES) mbar_wait(mbar_empty_base + stage * 8, empty_phase);
                    uint32_t st_base = smem_base + stage * STAGE_BYTES;
                    uint32_t full_bar = mbar_full_base + stage * 8;
                    mbar_expect_tx(full_bar, STAGE_BYTES);

                    int k_base = kt * BK;
                    int kb0 = kt * 2;
                    uint32_t h_ptr  = st_base;
                    uint32_t l_ptr  = h_ptr + H_BYTES;
                    uint32_t w_ptr  = l_ptr + L_BYTES;
                    uint32_t sh_ptr = w_ptr + W_BYTES;
                    uint32_t sw_ptr = sh_ptr + SH_BYTES;

                    tma_load_2d(h_ptr,               &tmapA_H, k_base,      m_base, full_bar);
                    tma_load_2d(h_ptr + STRIP_BYTES, &tmapA_H, k_base + 32, m_base, full_bar);
                    if constexpr (IS_DUAL) {
                        tma_load_2d(l_ptr,               &tmapA_L, k_base,      m_base, full_bar);
                        tma_load_2d(l_ptr + STRIP_BYTES, &tmapA_L, k_base + 32, m_base, full_bar);
                    }
                    if constexpr (!USE_W4) {
                        tma_load_2d(w_ptr + 0 * STRIP_BYTES, &tmapW, k_base,      n_base,       full_bar);
                        tma_load_2d(w_ptr + 1 * STRIP_BYTES, &tmapW, k_base,      n_base + 128, full_bar);
                        tma_load_2d(w_ptr + 2 * STRIP_BYTES, &tmapW, k_base + 32, n_base,       full_bar);
                        tma_load_2d(w_ptr + 3 * STRIP_BYTES, &tmapW, k_base + 32, n_base + 128, full_bar);
                    } else {
                        int wb_base = k_base >> 1;
                        tma_load_2d(w_ptr + 0 * STRIP_BYTES, &tmapW, wb_base, n_base,       full_bar);
                        tma_load_2d(w_ptr + 1 * STRIP_BYTES, &tmapW, wb_base, n_base + 128, full_bar);
                    }
                    tma_load_1d(sh_ptr,       sH_gmem + (size_t)kb0 * M + m_base,       128, full_bar);
                    tma_load_1d(sh_ptr + 128, sH_gmem + (size_t)(kb0 + 1) * M + m_base, 128, full_bar);
                    tma_load_1d(sw_ptr,       sW_gmem + (size_t)kb0 * N + n_base,       256, full_bar);
                    tma_load_1d(sw_ptr + 256, sW_gmem + (size_t)(kb0 + 1) * N + n_base, 256, full_bar);

                    stage++;
                    if (stage == STAGES) { stage = 0; empty_phase ^= 1; }
                }
            }
        } else {
            const __nv_bfloat16* abf_ptr = reinterpret_cast<const __nv_bfloat16*>(smem_raw + STAGES * STAGE_BYTES);
            for (int kt = 0; kt < num_tiles; ++kt) {
                uint32_t st_base = smem_base + stage * STAGE_BYTES;
                uint32_t full_bar = mbar_full_base + stage * 8;
                uint32_t w_ptr  = st_base + H_BYTES + L_BYTES;
                uint32_t sw_ptr = w_ptr + W_BYTES + SH_BYTES;

                if (pwid == 0 && lane == 0) {
                    if (kt >= STAGES) mbar_wait(mbar_empty_base + stage * 8, empty_phase);
                    int k_base = kt * BK;
                    int kb0 = kt * 2;
                    mbar_expect_tx(mbar_tma_a, ABF16_BYTES);
                    tma_load_2d(smem_abf16, &tmapA_H, k_base, m_base, mbar_tma_a);
                    mbar_expect_tx(full_bar, W_BYTES + SW_BYTES);
                    if constexpr (!USE_W4) {
                        tma_load_2d(w_ptr + 0 * STRIP_BYTES, &tmapW, k_base,      n_base,       full_bar);
                        tma_load_2d(w_ptr + 1 * STRIP_BYTES, &tmapW, k_base,      n_base + 128, full_bar);
                        tma_load_2d(w_ptr + 2 * STRIP_BYTES, &tmapW, k_base + 32, n_base,       full_bar);
                        tma_load_2d(w_ptr + 3 * STRIP_BYTES, &tmapW, k_base + 32, n_base + 128, full_bar);
                    } else {
                        int wb_base = k_base >> 1;
                        tma_load_2d(w_ptr + 0 * STRIP_BYTES, &tmapW, wb_base, n_base,       full_bar);
                        tma_load_2d(w_ptr + 1 * STRIP_BYTES, &tmapW, wb_base, n_base + 128, full_bar);
                    }
                    tma_load_1d(sw_ptr,       sW_gmem + (size_t)kb0 * N + n_base,       256, full_bar);
                    tma_load_1d(sw_ptr + 256, sW_gmem + (size_t)(kb0 + 1) * N + n_base, 256, full_bar);
                }

                mbar_wait(mbar_tma_a, tma_a_phase);
                tma_a_phase ^= 1;

                uint8_t* h_smem  = smem_raw + stage * STAGE_BYTES;
                uint8_t* l_smem  = h_smem + H_BYTES;
                uint8_t* sh_smem = h_smem + H_BYTES + L_BYTES + W_BYTES;
                int blk_w = lane >> 2;
                int q     = lane & 3;
                int c16   = q >> 1;
                int in16  = (q & 1) << 3;

                #pragma unroll
                for (int r_step = 0; r_step < 4; ++r_step) {
                    int r = pwid * 32 + r_step * 8 + blk_w;
                    int swz_off = r * 32 + swz32(r, c16) * 16 + in16;
                    #pragma unroll
                    for (int ks = 0; ks < 2; ++ks) {
                        uint4 raw = *reinterpret_cast<const uint4*>(abf_ptr + r * 64 + ks * 32 + (q << 3));
                        uint32_t sH_byte; uint2 h8, l8;
                        split_8bf16_quad(raw, sH_byte, h8, l8);
                        *reinterpret_cast<uint2*>(h_smem + ks * STRIP_BYTES + swz_off) = h8;
                        if constexpr (IS_DUAL) {
                            *reinterpret_cast<uint2*>(l_smem + ks * STRIP_BYTES + swz_off) = l8;
                        }
                        if (q == 0) sh_smem[ks * 128 + r] = (uint8_t)sH_byte;
                    }
                }
                mbar_arrive(full_bar);
                asm volatile("bar.sync 1, 128;\n" ::: "memory");

                stage++;
                if (stage == STAGES) { stage = 0; empty_phase ^= 1; }
            }
        }
    } else {
        warpgroup_reg_alloc<232>();
        int wm = warp_id >> 2, wn = warp_id & 3;
        int group = lane >> 2, t = lane & 3;

        float acc[4][N_SUB][4];
        #pragma unroll
        for (int mi = 0; mi < 4; ++mi)
            #pragma unroll
            for (int ni = 0; ni < N_SUB; ++ni)
                acc[mi][ni][0] = acc[mi][ni][1] = acc[mi][ni][2] = acc[mi][ni][3] = 0.0f;

        int a_ld_row = lane & 15, a_ld_c16 = (lane >> 4) & 1;
        int b_ld_row = ((lane >> 4) * 8) + (lane & 7), b_ld_c16 = (lane >> 3) & 1;
        uint32_t a_off[4];
        #pragma unroll
        for (int mi = 0; mi < 4; ++mi) {
            int r = wm * 64 + mi * 16 + a_ld_row;
            a_off[mi] = (uint32_t)(r * 32 + swz32(r, a_ld_c16) * 16);
        }
        int b_strip_id = wn >> 1, b_row_in_strip = (wn & 1) * 64;
        uint32_t b_off[N_SUB / 2];
        #pragma unroll
        for (int np = 0; np < N_SUB / 2; ++np) {
            int r = b_row_in_strip + np * 16 + b_ld_row;
            b_off[np] = (uint32_t)(r * 32 + swz32(r, b_ld_c16) * 16);
        }

        // Hardware-verified scale_vec::1X {0,0} lane mapping on SM 12.0:
        //   A scale: rows 0..7 on t=0 (lane 4*r), rows 8..15 on t=1 (lane 4*(r-8)+1), byte 0
        //   B scale: cols 0..7 on t=0 (lane 4*c), byte 0
        int a_sf_row_off = wm * 64 + group + ((t & 1) << 3);
        int b_sf_col_off = wn * WN + group;

        int stage = 0;
        uint32_t full_phase = 0;
        for (int kt = 0; kt < num_tiles; ++kt) {
            mbar_wait(mbar_full_base + stage * 8, full_phase);
            uint32_t st_base = smem_base + stage * STAGE_BYTES;
            uint32_t h_base  = st_base;
            uint32_t l_base  = h_base + H_BYTES;
            uint32_t w_base  = l_base + L_BYTES;
            const uint8_t* sh_ptr = smem_raw + stage * STAGE_BYTES + H_BYTES + L_BYTES + W_BYTES;
            const uint8_t* sw_ptr = sh_ptr + SH_BYTES;

            if constexpr (!USE_W4) {
                #pragma unroll
                for (int ks = 0; ks < 2; ++ks) {
                    uint32_t h_strip = h_base + ks * STRIP_BYTES;
                    uint32_t l_strip = l_base + ks * STRIP_BYTES;
                    uint32_t w_strip = w_base + (ks * 2 + b_strip_id) * STRIP_BYTES;

                    uint32_t b_f[N_SUB][2];
                    uint32_t sB[N_SUB];
                    #pragma unroll
                    for (int np = 0; np < N_SUB / 2; ++np)
                        ldmatrix_x4(b_f[np*2][0], b_f[np*2][1], b_f[np*2+1][0], b_f[np*2+1][1], w_strip + b_off[np]);
                    #pragma unroll
                    for (int ni = 0; ni < N_SUB; ++ni)
                        sB[ni] = (uint32_t)sw_ptr[ks * 256 + b_sf_col_off + ni * 8];

                    #pragma unroll
                    for (int mi = 0; mi < 4; ++mi) {
                        uint32_t ah0, ah1, ah2, ah3;
                        ldmatrix_x4(ah0, ah1, ah2, ah3, h_strip + a_off[mi]);
                        uint32_t sA_H = (uint32_t)sh_ptr[ks * 128 + a_sf_row_off + mi * 16];
                        #pragma unroll
                        for (int ni = 0; ni < N_SUB; ++ni) {
                            asm volatile("mma.sync.aligned.kind::mxf8f6f4.block_scale.scale_vec::1X.m16n8k32.row.col.f32.e4m3.e4m3.f32.ue8m0 {%0,%1,%2,%3},{%4,%5,%6,%7},{%8,%9},{%0,%1,%2,%3},%10,{0,0},%11,{0,0};\n"
                                : "+f"(acc[mi][ni][0]),"+f"(acc[mi][ni][1]),"+f"(acc[mi][ni][2]),"+f"(acc[mi][ni][3])
                                : "r"(ah0),"r"(ah1),"r"(ah2),"r"(ah3),"r"(b_f[ni][0]),"r"(b_f[ni][1]),"r"(sA_H),"r"(sB[ni]));
                        }
                        if constexpr (IS_DUAL) {
                            uint32_t al0, al1, al2, al3;
                            ldmatrix_x4(al0, al1, al2, al3, l_strip + a_off[mi]);
                            uint32_t sA_L = sA_H - 4u;
                            #pragma unroll
                            for (int ni = 0; ni < N_SUB; ++ni) {
                                asm volatile("mma.sync.aligned.kind::mxf8f6f4.block_scale.scale_vec::1X.m16n8k32.row.col.f32.e4m3.e4m3.f32.ue8m0 {%0,%1,%2,%3},{%4,%5,%6,%7},{%8,%9},{%0,%1,%2,%3},%10,{0,0},%11,{0,0};\n"
                                    : "+f"(acc[mi][ni][0]),"+f"(acc[mi][ni][1]),"+f"(acc[mi][ni][2]),"+f"(acc[mi][ni][3])
                                    : "r"(al0),"r"(al1),"r"(al2),"r"(al3),"r"(b_f[ni][0]),"r"(b_f[ni][1]),"r"(sA_L),"r"(sB[ni]));
                            }
                        }
                    }
                }
            } else {
                // W4A16: single ldmatrix.x4 loads interleaved E2M1 for both ks=0 and ks=1;
                // E2M1 nibbles unpack to bits [5:2] of each byte via two shift+mask ops.
                uint32_t w_strip = w_base + b_strip_id * STRIP_BYTES;
                uint32_t wp[2][N_SUB];
                #pragma unroll
                for (int np = 0; np < N_SUB / 2; ++np)
                    ldmatrix_x4(wp[0][np*2], wp[1][np*2], wp[0][np*2+1], wp[1][np*2+1], w_strip + b_off[np]);

                #pragma unroll
                for (int ks = 0; ks < 2; ++ks) {
                    uint32_t h_strip = h_base + ks * STRIP_BYTES;
                    uint32_t l_strip = l_base + ks * STRIP_BYTES;
                    uint32_t b_f[N_SUB][2];
                    uint32_t sB[N_SUB];
                    #pragma unroll
                    for (int ni = 0; ni < N_SUB; ++ni) {
                        uint32_t p = wp[ks][ni];
                        b_f[ni][0] = (p << 2) & 0x3C3C3C3Cu;
                        b_f[ni][1] = (p >> 2) & 0x3C3C3C3Cu;
                        sB[ni] = (uint32_t)sw_ptr[ks * 256 + b_sf_col_off + ni * 8];
                    }
                    #pragma unroll
                    for (int mi = 0; mi < 4; ++mi) {
                        uint32_t ah0, ah1, ah2, ah3;
                        ldmatrix_x4(ah0, ah1, ah2, ah3, h_strip + a_off[mi]);
                        uint32_t sA_H = (uint32_t)sh_ptr[ks * 128 + a_sf_row_off + mi * 16];
                        #pragma unroll
                        for (int ni = 0; ni < N_SUB; ++ni) {
                            asm volatile("mma.sync.aligned.kind::mxf8f6f4.block_scale.scale_vec::1X.m16n8k32.row.col.f32.e4m3.e2m1.f32.ue8m0 {%0,%1,%2,%3},{%4,%5,%6,%7},{%8,%9},{%0,%1,%2,%3},%10,{0,0},%11,{0,0};\n"
                                : "+f"(acc[mi][ni][0]),"+f"(acc[mi][ni][1]),"+f"(acc[mi][ni][2]),"+f"(acc[mi][ni][3])
                                : "r"(ah0),"r"(ah1),"r"(ah2),"r"(ah3),"r"(b_f[ni][0]),"r"(b_f[ni][1]),"r"(sA_H),"r"(sB[ni]));
                        }
                        if constexpr (IS_DUAL) {
                            uint32_t al0, al1, al2, al3;
                            ldmatrix_x4(al0, al1, al2, al3, l_strip + a_off[mi]);
                            uint32_t sA_L = sA_H - 4u;
                            #pragma unroll
                            for (int ni = 0; ni < N_SUB; ++ni) {
                                asm volatile("mma.sync.aligned.kind::mxf8f6f4.block_scale.scale_vec::1X.m16n8k32.row.col.f32.e4m3.e2m1.f32.ue8m0 {%0,%1,%2,%3},{%4,%5,%6,%7},{%8,%9},{%0,%1,%2,%3},%10,{0,0},%11,{0,0};\n"
                                    : "+f"(acc[mi][ni][0]),"+f"(acc[mi][ni][1]),"+f"(acc[mi][ni][2]),"+f"(acc[mi][ni][3])
                                    : "r"(al0),"r"(al1),"r"(al2),"r"(al3),"r"(b_f[ni][0]),"r"(b_f[ni][1]),"r"(sA_L),"r"(sB[ni]));
                            }
                        }
                    }
                }
            }

            mbar_arrive(mbar_empty_base + stage * 8);
            stage++;
            if (stage == STAGES) { stage = 0; full_phase ^= 1; }
        }

        int t2 = t * 2;
        #pragma unroll
        for (int mi = 0; mi < 4; ++mi) {
            int r0 = m_base + wm * 64 + mi * 16 + group, r1 = r0 + 8;
            #pragma unroll
            for (int ni = 0; ni < N_SUB; ++ni) {
                int c0 = n_base + wn * WN + ni * 8 + t2;
                *reinterpret_cast<float2*>(&C[(size_t)r0 * N + c0]) = make_float2(acc[mi][ni][0], acc[mi][ni][1]);
                *reinterpret_cast<float2*>(&C[(size_t)r1 * N + c0]) = make_float2(acc[mi][ni][2], acc[mi][ni][3]);
            }
        }
    }
}

static CUtensorMap make_tmap_u8_32b(void* ptr, int rows, int cols_bytes) {
    CUtensorMap tmap{};
    cuuint64_t gdim[2] = { (cuuint64_t)cols_bytes, (cuuint64_t)rows };
    cuuint64_t gstr[1] = { (cuuint64_t)cols_bytes };
    cuuint32_t bdim[2] = { 32, 128 }, estr[2] = { 1, 1 };
    CHECK_CU(cuTensorMapEncodeTiled(&tmap, CU_TENSOR_MAP_DATA_TYPE_UINT8, 2, ptr, gdim, gstr, bdim, estr,
        CU_TENSOR_MAP_INTERLEAVE_NONE, CU_TENSOR_MAP_SWIZZLE_32B, CU_TENSOR_MAP_L2_PROMOTION_L2_128B, CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE));
    return tmap;
}

static CUtensorMap make_tmap_bf16_64(void* ptr, int rows, int K) {
    CUtensorMap tmap{};
    cuuint64_t gdim[2] = { (cuuint64_t)K, (cuuint64_t)rows };
    cuuint64_t gstr[1] = { (cuuint64_t)(K * 2) };
    cuuint32_t bdim[2] = { 64, 128 }, estr[2] = { 1, 1 };
    CHECK_CU(cuTensorMapEncodeTiled(&tmap, CU_TENSOR_MAP_DATA_TYPE_BFLOAT16, 2, ptr, gdim, gstr, bdim, estr,
        CU_TENSOR_MAP_INTERLEAVE_NONE, CU_TENSOR_MAP_SWIZZLE_NONE, CU_TENSOR_MAP_L2_PROMOTION_L2_128B, CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE));
    return tmap;
}

static double e4m3_to_double(uint8_t b) {
    int s = (b >> 7) & 1, e = (b >> 3) & 0xF, m = b & 0x7;
    if (e == 15 && m == 7) return NAN;
    double v = (e == 0) ? ldexp((double)m, -9) : ldexp(1.0 + (double)m / 8.0, e - 7);
    return s ? -v : v;
}
static double e2m1_to_double(uint8_t nib) {
    int s = (nib >> 3) & 1, e = (nib >> 1) & 3, m = nib & 1;
    double v = (e == 0) ? (m ? 0.5 : 0.0) : ldexp(1.0 + 0.5 * m, e - 1);
    return s ? -v : v;
}
static uint8_t f32_to_e4m3(float x) {
    __nv_fp8_e4m3 f = __nv_fp8_e4m3(x);
    return *reinterpret_cast<uint8_t*>(&f);
}

struct NvmlMonitor {
    nvmlDevice_t dev{};
    std::atomic<bool> running{false};
    std::thread th;
    double sum_clk = 0, sum_pwr = 0;
    int samples = 0;
    void init() { nvmlInit_v2(); nvmlDeviceGetHandleByIndex_v2(0, &dev); }
    void start() {
        sum_clk = 0; sum_pwr = 0; samples = 0; running = true;
        th = std::thread([&]() {
            while (running.load(std::memory_order_relaxed)) {
                unsigned int clk = 0, pwr = 0;
                if (nvmlDeviceGetClockInfo(dev, NVML_CLOCK_SM, &clk) == NVML_SUCCESS &&
                    nvmlDeviceGetPowerUsage(dev, &pwr) == NVML_SUCCESS) {
                    sum_clk += (double)clk; sum_pwr += (double)pwr / 1000.0; samples++;
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(15));
            }
        });
    }
    void stop(double& ghz, double& w) {
        running = false; if (th.joinable()) th.join();
        ghz = (samples > 0) ? (sum_clk / samples / 1000.0) : 0.0;
        w   = (samples > 0) ? (sum_pwr / samples) : 0.0;
    }
    void shutdown() { nvmlShutdown(); }
};

template<bool USE_W4, bool IS_DUAL, bool ONLINE_SPLIT>
static size_t calc_smem() {
    size_t H = 8192, L = IS_DUAL ? 8192 : 0, W = USE_W4 ? 8192 : 16384;
    size_t st = H + L + W + 256 + 512;
    size_t abf = ONLINE_SPLIT ? 16384 : 0;
    return 2 * st + abf + 64;
}

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    const char* json_path = nullptr;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--json") == 0 && i + 1 < argc) json_path = argv[++i];
    }

    CHECK_CU(cuInit(0));
    cudaDeviceProp prop; cudaGetDeviceProperties(&prop, 0);
    int sms = prop.multiProcessorCount;
    printf("GPU: %s (SMs=%d, cc=%d.%d)\n\n", prop.name, sms, prop.major, prop.minor);
    NvmlMonitor nvml; nvml.init();

    auto set_smem = [](auto fn, size_t sz) {
        CHECK_CUDA(cudaFuncSetAttribute(fn, cudaFuncAttributeMaxDynamicSharedMemorySize, (int)sz));
    };
    set_smem(gemm_qmma_sf_tma_ws<false, false, false>, calc_smem<false, false, false>());
    set_smem(gemm_qmma_sf_tma_ws<false, true,  false>, calc_smem<false, true,  false>());
    set_smem(gemm_qmma_sf_tma_ws<false, true,  true>,  calc_smem<false, true,  true>());
    set_smem(gemm_qmma_sf_tma_ws<true,  false, false>, calc_smem<true,  false, false>());
    set_smem(gemm_qmma_sf_tma_ws<true,  true,  false>, calc_smem<true,  true,  false>());
    set_smem(gemm_qmma_sf_tma_ws<true,  true,  true>,  calc_smem<true,  true,  true>());

    cublasHandle_t handle; cublasCreate(&handle);

    struct Shape { const char* name; int M, N, K; };
    std::vector<Shape> shapes = {
        {"Llama8B Prefill Q/O (4096x4096x4096)",   4096,  4096, 4096},
        {"Llama8B Prefill Up  (4096x14336x4096)",  4096, 14336, 4096},
        {"Llama8B Batch-512   (512x14336x4096)",    512, 14336, 4096},
    };

    FILE* jf = json_path ? fopen(json_path, "w") : nullptr;
    if (jf) fprintf(jf, "{\n  \"gpu\": \"%s\", \"sms\": %d, \"shapes\": [\n", prop.name, sms);

    for (size_t si = 0; si < shapes.size(); ++si) {
        auto& sh = shapes[si];
        int M = sh.M, N = sh.N, K = sh.K, num_kb = K / 32;
        printf("=== %s (1.0s pre-burn + 1.0s steady-state NVML) ===\n", sh.name);

        std::mt19937 rng(42 + (int)si);
        std::normal_distribution<float> dist(0.0f, 0.25f);
        std::vector<__nv_bfloat16> hA_bf((size_t)M * K);
        std::vector<__half> hA_f16((size_t)M * K), hW8_f16((size_t)N * K);
        std::vector<__nv_bfloat16> hW8_bf((size_t)N * K), hW4_bf((size_t)N * K);
        std::vector<uint8_t> hW8((size_t)N * K), hW4_pack((size_t)N * (K / 2));
        std::vector<uint8_t> hsW8((size_t)num_kb * N), hsW4((size_t)num_kb * N);

        for (size_t i = 0; i < hA_bf.size(); ++i) {
            float v = dist(rng);
            hA_bf[i]  = __float2bfloat16(v);
            hA_f16[i] = __float2half(v);
        }

        const float e2m1_vals[8] = {0.0f, 0.5f, 1.0f, 1.5f, 2.0f, 3.0f, 4.0f, 6.0f};
        for (int r = 0; r < N; ++r) {
            for (int kb = 0; kb < num_kb; ++kb) {
                float blk[32], amax = 0.0f;
                for (int j = 0; j < 32; ++j) { blk[j] = dist(rng); amax = std::max(amax, fabsf(blk[j])); }
                int em8 = (amax > 0) ? (int)floorf(log2f(amax)) : 0;
                int sh8 = em8 - 7;
                hsW8[(size_t)kb * N + r] = (uint8_t)(127 + sh8);
                float inv8 = ldexpf(1.0f, -sh8), s8 = ldexpf(1.0f, sh8);
                int sh4 = (amax > 0) ? (int)floorf(log2f(amax / 3.0f)) : 0;
                hsW4[(size_t)kb * N + r] = (uint8_t)(127 + sh4);
                float inv4 = ldexpf(1.0f, -sh4), s4 = ldexpf(1.0f, sh4);

                uint8_t w4_nibs[32];
                for (int j = 0; j < 32; ++j) {
                    uint8_t q8 = f32_to_e4m3(blk[j] * inv8);
                    hW8[(size_t)r * K + kb * 32 + j] = q8;
                    float dq8 = (float)(e4m3_to_double(q8) * (double)s8);
                    hW8_bf[(size_t)r * K + kb * 32 + j]  = __float2bfloat16(dq8);
                    hW8_f16[(size_t)r * K + kb * 32 + j] = __float2half(dq8);

                    float v4 = blk[j] * inv4;
                    uint8_t sign = (v4 < 0.0f) ? 8u : 0u;
                    float av = fabsf(v4);
                    int best = 0; float bd = 1e9f;
                    for (int c = 0; c < 8; ++c) { float d = fabsf(av - e2m1_vals[c]); if (d < bd) { bd = d; best = c; } }
                    uint8_t nib = sign | (uint8_t)best;
                    w4_nibs[j] = nib;
                    float dq4 = (float)(e2m1_to_double(nib) * (double)s4);
                    hW4_bf[(size_t)r * K + kb * 32 + j] = __float2bfloat16(dq4);
                }
                uint8_t* dst = &hW4_pack[(size_t)r * (K / 2) + kb * 16];
                for (int j = 0; j < 16; ++j)
                    dst[j] = (w4_nibs[j] & 0xFu) | ((w4_nibs[16 + j] & 0xFu) << 4);
            }
        }

        __nv_bfloat16 *dA_bf, *dW8_bf, *dW4_bf;
        __half *dA_f16, *dW8_f16, *dC16;
        uint8_t *dH, *dL, *dsH, *dW8, *dW4p, *dsW8, *dsW4;
        float *dC_ref8, *dC_ref4, *dC_out;
        CHECK_CUDA(cudaMalloc(&dA_bf, (size_t)M*K*2));
        CHECK_CUDA(cudaMalloc(&dW8_bf, (size_t)N*K*2));
        CHECK_CUDA(cudaMalloc(&dW4_bf, (size_t)N*K*2));
        CHECK_CUDA(cudaMalloc(&dA_f16, (size_t)M*K*2));
        CHECK_CUDA(cudaMalloc(&dW8_f16, (size_t)N*K*2));
        CHECK_CUDA(cudaMalloc(&dC16, (size_t)M*N*2));
        CHECK_CUDA(cudaMalloc(&dH, (size_t)M*K));
        CHECK_CUDA(cudaMalloc(&dL, (size_t)M*K));
        CHECK_CUDA(cudaMalloc(&dsH, (size_t)num_kb*M));
        CHECK_CUDA(cudaMalloc(&dW8, (size_t)N*K));
        CHECK_CUDA(cudaMalloc(&dW4p, (size_t)N*(K/2)));
        CHECK_CUDA(cudaMalloc(&dsW8, (size_t)num_kb*N));
        CHECK_CUDA(cudaMalloc(&dsW4, (size_t)num_kb*N));
        CHECK_CUDA(cudaMalloc(&dC_ref8, (size_t)M*N*4));
        CHECK_CUDA(cudaMalloc(&dC_ref4, (size_t)M*N*4));
        CHECK_CUDA(cudaMalloc(&dC_out, (size_t)M*N*4));

        CHECK_CUDA(cudaMemcpy(dA_bf, hA_bf.data(), (size_t)M*K*2, cudaMemcpyHostToDevice));
        CHECK_CUDA(cudaMemcpy(dW8_bf, hW8_bf.data(), (size_t)N*K*2, cudaMemcpyHostToDevice));
        CHECK_CUDA(cudaMemcpy(dW4_bf, hW4_bf.data(), (size_t)N*K*2, cudaMemcpyHostToDevice));
        CHECK_CUDA(cudaMemcpy(dA_f16, hA_f16.data(), (size_t)M*K*2, cudaMemcpyHostToDevice));
        CHECK_CUDA(cudaMemcpy(dW8_f16, hW8_f16.data(), (size_t)N*K*2, cudaMemcpyHostToDevice));
        CHECK_CUDA(cudaMemcpy(dW8, hW8.data(), (size_t)N*K, cudaMemcpyHostToDevice));
        CHECK_CUDA(cudaMemcpy(dW4p, hW4_pack.data(), (size_t)N*(K/2), cudaMemcpyHostToDevice));
        CHECK_CUDA(cudaMemcpy(dsW8, hsW8.data(), (size_t)num_kb*N, cudaMemcpyHostToDevice));
        CHECK_CUDA(cudaMemcpy(dsW4, hsW4.data(), (size_t)num_kb*N, cudaMemcpyHostToDevice));

        int split_blocks = ((M * num_kb) + 63) / 64;
        split_bf16_to_dual_fp8_kernel<<<split_blocks, 256>>>(dA_bf, dH, dL, dsH, M, K);
        CHECK_CUDA(cudaDeviceSynchronize());

        CUtensorMap tmH    = make_tmap_u8_32b(dH, M, K);
        CUtensorMap tmL    = make_tmap_u8_32b(dL, M, K);
        CUtensorMap tmAbf  = make_tmap_bf16_64(dA_bf, M, K);
        CUtensorMap tmW8   = make_tmap_u8_32b(dW8, N, K);
        CUtensorMap tmW4   = make_tmap_u8_32b(dW4p, N, K / 2);

        float alpha = 1.0f, beta = 0.0f;
        __half alpha16 = __float2half(1.0f), beta16 = __float2half(0.0f);
        double flops = 2.0 * (double)M * (double)N * (double)K;
        int grid = (M / 128) * (N / 256);

        cublasGemmEx(handle, CUBLAS_OP_T, CUBLAS_OP_N, N, M, K, &alpha, dW8_bf, CUDA_R_16BF, K, dA_bf, CUDA_R_16BF, K, &beta, dC_ref8, CUDA_R_32F, N, CUBLAS_COMPUTE_32F, CUBLAS_GEMM_DEFAULT);
        cublasGemmEx(handle, CUBLAS_OP_T, CUBLAS_OP_N, N, M, K, &alpha, dW4_bf, CUDA_R_16BF, K, dA_bf, CUDA_R_16BF, K, &beta, dC_ref4, CUDA_R_32F, N, CUBLAS_COMPUTE_32F, CUBLAS_GEMM_DEFAULT);
        CHECK_CUDA(cudaDeviceSynchronize());

        std::vector<double> ref8_f64(16 * 64, 0.0), ref4_f64(16 * 64, 0.0);
        for (int r = 0; r < 16; ++r) {
            for (int c = 0; c < 64; ++c) {
                double s8 = 0.0, s4 = 0.0;
                for (int k = 0; k < K; ++k) {
                    double a = (double)__bfloat162float(hA_bf[(size_t)r * K + k]);
                    s8 += a * (double)__bfloat162float(hW8_bf[(size_t)c * K + k]);
                    s4 += a * (double)__bfloat162float(hW4_bf[(size_t)c * K + k]);
                }
                ref8_f64[r * 64 + c] = s8;
                ref4_f64[r * 64 + c] = s4;
            }
        }

        if (jf) fprintf(jf, "    {\"name\": \"%s\", \"M\": %d, \"N\": %d, \"K\": %d, \"results\": [\n", sh.name, M, N, K);
        bool first_res = true;

        auto bench_steady = [&](const char* lbl, auto launch, const float* d_ref, const std::vector<double>* ref_f64, float* d_out) {
            cudaEvent_t e0, e1; cudaEventCreate(&e0); cudaEventCreate(&e1);
            for (int w = 0; w < 3; ++w) launch();
            CHECK_CUDA(cudaDeviceSynchronize());
            cudaEventRecord(e0); launch(); cudaEventRecord(e1); cudaEventSynchronize(e1);
            float ms1 = 1.0f; cudaEventElapsedTime(&ms1, e0, e1);
            int reps_1s = std::max(20, std::min(800, (int)(900.0f / std::max(ms1, 0.3f))));

            for (int r = 0; r < reps_1s; ++r) launch();
            CHECK_CUDA(cudaDeviceSynchronize());

            nvml.start();
            cudaEventRecord(e0);
            for (int r = 0; r < reps_1s; ++r) launch();
            cudaEventRecord(e1); cudaEventSynchronize(e1);
            double ghz = 0, w = 0; nvml.stop(ghz, w);
            float ms = 0; cudaEventElapsedTime(&ms, e0, e1);
            double tf = (flops * reps_1s) / (ms * 1e9);
            double tf_ghz = (ghz > 0.1) ? (tf / ghz) : 0.0;
            double fl_clk = tf_ghz * 1000.0 / (double)sms;

            double l2_cublas = -1.0, l2_f64 = -1.0;
            if (d_ref && d_out) {
                int samp = std::min((int)((size_t)M*N), 65536);
                std::vector<float> ref(samp), out(samp);
                cudaMemcpy(ref.data(), d_ref, samp*4, cudaMemcpyDeviceToHost);
                cudaMemcpy(out.data(), d_out, samp*4, cudaMemcpyDeviceToHost);
                double e2=0, r2=0;
                for (int i = 0; i < samp; ++i) { double e=(double)out[i]-(double)ref[i]; e2+=e*e; r2+=(double)ref[i]*(double)ref[i]; }
                l2_cublas = sqrt(e2 / std::max(r2, 1e-30));
            }
            if (ref_f64 && d_out) {
                std::vector<float> patch(16 * N);
                cudaMemcpy(patch.data(), d_out, (size_t)16 * N * 4, cudaMemcpyDeviceToHost);
                double e2=0, r2=0;
                for (int r = 0; r < 16; ++r)
                    for (int c = 0; c < 64; ++c) {
                        double e = (double)patch[r * N + c] - (*ref_f64)[r * 64 + c];
                        e2 += e*e; r2 += (*ref_f64)[r * 64 + c] * (*ref_f64)[r * 64 + c];
                    }
                l2_f64 = sqrt(e2 / std::max(r2, 1e-30));
            }
            if (l2_f64 >= 0.0)
                printf("  %-34s | %6.2f TF/s | %5.2f TF/GHz (%6.1f/clk) | %.2fGHz %5.1fW | L2_f64=%.2e\n",
                       lbl, tf, tf_ghz, fl_clk, ghz, w, l2_f64);
            else
                printf("  %-34s | %6.2f TF/s | %5.2f TF/GHz (%6.1f/clk) | %.2fGHz %5.1fW |\n",
                       lbl, tf, tf_ghz, fl_clk, ghz, w);

            if (jf) {
                fprintf(jf, "%s      {\"label\": \"%s\", \"tflops\": %.4f, \"tflops_per_ghz\": %.4f, \"flop_sm_clk\": %.2f, \"ghz\": %.4f, \"watts\": %.2f, \"l2_vs_cublas\": %.3e, \"l2_vs_fp64\": %.3e}",
                        first_res ? "" : ",\n", lbl, tf, tf_ghz, fl_clk, ghz, w, l2_cublas, l2_f64);
            }
            first_res = false;
            cudaEventDestroy(e0); cudaEventDestroy(e1);
            return std::make_pair(tf, fl_clk);
        };

        auto [tf_bf, fl_bf] = bench_steady("cuBLAS BF16->FP32 (W8 dequant)", [&](){
            cublasGemmEx(handle, CUBLAS_OP_T, CUBLAS_OP_N, N, M, K, &alpha, dW8_bf, CUDA_R_16BF, K, dA_bf, CUDA_R_16BF, K, &beta, dC_out, CUDA_R_32F, N, CUBLAS_COMPUTE_32F, CUBLAS_GEMM_DEFAULT);
        }, dC_ref8, &ref8_f64, dC_out);

        bench_steady("cuBLAS FP16->FP16 (1024 ceiling)", [&](){
            cublasGemmEx(handle, CUBLAS_OP_T, CUBLAS_OP_N, N, M, K, &alpha16, dW8_f16, CUDA_R_16F, K, dA_f16, CUDA_R_16F, K, &beta16, dC16, CUDA_R_16F, N, CUBLAS_COMPUTE_16F, CUBLAS_GEMM_DEFAULT);
        }, nullptr, nullptr, nullptr);

        size_t sm_w8_s = calc_smem<false, false, false>();
        size_t sm_w8_p = calc_smem<false, true,  false>();
        size_t sm_w8_o = calc_smem<false, true,  true>();
        bench_steady("TMA-WS Single-FP8 W8A8 (pre)", [&](){
            gemm_qmma_sf_tma_ws<false, false, false><<<grid, 384, sm_w8_s>>>(tmH, tmL, tmW8, dsH, dsW8, dC_out, M, N, K);
        }, dC_ref8, &ref8_f64, dC_out);
        auto [tf_w8p, fl_w8p] = bench_steady("TMA-WS Dual-FP8 W8A16 (pre-split)", [&](){
            gemm_qmma_sf_tma_ws<false, true, false><<<grid, 384, sm_w8_p>>>(tmH, tmL, tmW8, dsH, dsW8, dC_out, M, N, K);
        }, dC_ref8, &ref8_f64, dC_out);
        auto [tf_w8t, fl_w8t] = bench_steady("TMA-WS Dual-FP8 W8A16 (2-kernel)", [&](){
            split_bf16_to_dual_fp8_kernel<<<split_blocks, 256>>>(dA_bf, dH, dL, dsH, M, K);
            gemm_qmma_sf_tma_ws<false, true, false><<<grid, 384, sm_w8_p>>>(tmH, tmL, tmW8, dsH, dsW8, dC_out, M, N, K);
        }, dC_ref8, &ref8_f64, dC_out);
        auto [tf_w8o, fl_w8o] = bench_steady("TMA-WS Dual-FP8 W8A16 (online)", [&](){
            gemm_qmma_sf_tma_ws<false, true, true><<<grid, 384, sm_w8_o>>>(tmAbf, tmL, tmW8, dsH, dsW8, dC_out, M, N, K);
        }, dC_ref8, &ref8_f64, dC_out);

        size_t sm_w4_s = calc_smem<true, false, false>();
        size_t sm_w4_p = calc_smem<true, true,  false>();
        size_t sm_w4_o = calc_smem<true, true,  true>();
        bench_steady("TMA-WS Single-FP8 W4A8 (pre)", [&](){
            gemm_qmma_sf_tma_ws<true, false, false><<<grid, 384, sm_w4_s>>>(tmH, tmL, tmW4, dsH, dsW4, dC_out, M, N, K);
        }, dC_ref4, &ref4_f64, dC_out);
        auto [tf_w4p, fl_w4p] = bench_steady("TMA-WS Dual-FP8 W4A16 (pre-split)", [&](){
            gemm_qmma_sf_tma_ws<true, true, false><<<grid, 384, sm_w4_p>>>(tmH, tmL, tmW4, dsH, dsW4, dC_out, M, N, K);
        }, dC_ref4, &ref4_f64, dC_out);
        auto [tf_w4t, fl_w4t] = bench_steady("TMA-WS Dual-FP8 W4A16 (2-kernel)", [&](){
            split_bf16_to_dual_fp8_kernel<<<split_blocks, 256>>>(dA_bf, dH, dL, dsH, M, K);
            gemm_qmma_sf_tma_ws<true, true, false><<<grid, 384, sm_w4_p>>>(tmH, tmL, tmW4, dsH, dsW4, dC_out, M, N, K);
        }, dC_ref4, &ref4_f64, dC_out);
        auto [tf_w4o, fl_w4o] = bench_steady("TMA-WS Dual-FP8 W4A16 (online)", [&](){
            gemm_qmma_sf_tma_ws<true, true, true><<<grid, 384, sm_w4_o>>>(tmAbf, tmL, tmW4, dsH, dsW4, dC_out, M, N, K);
        }, dC_ref4, &ref4_f64, dC_out);

        printf("  >>> SPEEDUP vs cuBLAS BF16: W8A16 pre=%.2fx wall / %.2fx iso-clk | 2-kern=%.2fx / %.2fx | online=%.2fx / %.2fx\n",
               tf_w8p/tf_bf, fl_w8p/fl_bf, tf_w8t/tf_bf, fl_w8t/fl_bf, tf_w8o/tf_bf, fl_w8o/fl_bf);
        printf("  >>> SPEEDUP vs cuBLAS BF16: W4A16 pre=%.2fx wall / %.2fx iso-clk | 2-kern=%.2fx / %.2fx | online=%.2fx / %.2fx\n\n",
               tf_w4p/tf_bf, fl_w4p/fl_bf, tf_w4t/tf_bf, fl_w4t/fl_bf, tf_w4o/tf_bf, fl_w4o/fl_bf);

        if (jf) fprintf(jf, "\n    ]}%s\n", (si + 1 < shapes.size()) ? "," : "");

        cudaFree(dA_bf); cudaFree(dW8_bf); cudaFree(dW4_bf); cudaFree(dA_f16); cudaFree(dW8_f16); cudaFree(dC16);
        cudaFree(dH); cudaFree(dL); cudaFree(dsH); cudaFree(dW8); cudaFree(dW4p); cudaFree(dsW8); cudaFree(dsW4);
        cudaFree(dC_ref8); cudaFree(dC_ref4); cudaFree(dC_out);
    }

    if (jf) { fprintf(jf, "  ]\n}\n"); fclose(jf); }
    cublasDestroy(handle); nvml.shutdown();
    return 0;
}
