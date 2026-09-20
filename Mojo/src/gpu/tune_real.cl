// Experiment B — work-group size / vectorization tuning (Gen9 OpenCL
// optimization guide, triage item 8 ADOPT). Same grid-stride reduce skeleton
// as D/A, three variables:
//   vector load width: float4 (baseline, 16B) / float8 (32B) / float16 (64B)
//   work-group size: 64 / 128 / 256 (clinfo max on this box)
//   playbook variant: float4 + uint counters + #pragma unroll (Gen9 guide:
//     unsigned loop counters, unrolled grid stride, SLM-multiple group size)
// Output: one f32 per work-group (sum + sumsq), reduced on host like D/A.
// Gen9 note: 24 EUs in 3 subslices of 8; the guide prefers work-group sizes
// that tile the EU count — 64/128/256 cover 2.67/5.33/10.67 EUs per group's
// resident threads; the sweep tells us which (if any) the driver honors.

#define UNROLL 4

__kernel void tune_f4(__global const float *input,
                      __global float *sum_out,
                      __global float *sumsq_out,
                      const int n) {
    __local float sdata[256];
    __local float sqdata[256];
    int tid = get_local_id(0);
    int gid = get_global_id(0);
    int gsize = get_global_size(0);

    float4 acc = (float4)(0.0f, 0.0f, 0.0f, 0.0f);
    float4 accsq = (float4)(0.0f, 0.0f, 0.0f, 0.0f);
    int n4 = n >> 2;
    for (int i4 = gid; i4 < n4; i4 += gsize) {
        float4 v = ((__global const float4 *)input)[i4];
        acc += v;
        accsq += v * v;
    }
    sdata[tid] = acc.x + acc.y + acc.z + acc.w;
    sqdata[tid] = accsq.x + accsq.y + accsq.z + accsq.w;
    barrier(CLK_LOCAL_MEM_FENCE);

    int ls = get_local_size(0);
    for (int s = ls / 2; s > 0; s >>= 1) {
        if (tid < s) {
            sdata[tid] += sdata[tid + s];
            sqdata[tid] += sqdata[tid + s];
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }

    if (tid == 0) {
        sum_out[get_group_id(0)] = sdata[0];
        sumsq_out[get_group_id(0)] = sqdata[0];
    }
}

__kernel void tune_f8(__global const float *input,
                      __global float *sum_out,
                      __global float *sumsq_out,
                      const int n) {
    __local float sdata[256];
    __local float sqdata[256];
    int tid = get_local_id(0);
    int gid = get_global_id(0);
    int gsize = get_global_size(0);

    float8 acc = (float8)(0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f);
    float8 accsq = (float8)(0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f);
    int n8 = n >> 3;
    for (int i8 = gid; i8 < n8; i8 += gsize) {
        float8 v = ((__global const float8 *)input)[i8];
        acc += v;
        accsq += v * v;
    }
    sdata[tid] = acc.s0 + acc.s1 + acc.s2 + acc.s3 + acc.s4 + acc.s5 + acc.s6 + acc.s7;
    sqdata[tid] = accsq.s0 + accsq.s1 + accsq.s2 + accsq.s3 + accsq.s4 + accsq.s5 + accsq.s6 + accsq.s7;
    barrier(CLK_LOCAL_MEM_FENCE);

    int ls = get_local_size(0);
    for (int s = ls / 2; s > 0; s >>= 1) {
        if (tid < s) {
            sdata[tid] += sdata[tid + s];
            sqdata[tid] += sqdata[tid + s];
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }

    if (tid == 0) {
        sum_out[get_group_id(0)] = sdata[0];
        sumsq_out[get_group_id(0)] = sqdata[0];
    }
}

__kernel void tune_f16(__global const float *input,
                       __global float *sum_out,
                       __global float *sumsq_out,
                       const int n) {
    __local float sdata[256];
    __local float sqdata[256];
    int tid = get_local_id(0);
    int gid = get_global_id(0);
    int gsize = get_global_size(0);

    float16 acc = (float16)0.0f;
    float16 accsq = (float16)0.0f;
    int n16 = n >> 4;
    for (int i16 = gid; i16 < n16; i16 += gsize) {
        float16 v = ((__global const float16 *)input)[i16];
        acc += v;
        accsq += v * v;
    }
    sdata[tid] = acc.s0 + acc.s1 + acc.s2 + acc.s3 + acc.s4 + acc.s5 + acc.s6 + acc.s7 +
                 acc.s8 + acc.s9 + acc.sA + acc.sB + acc.sC + acc.sD + acc.sE + acc.sF;
    sqdata[tid] = accsq.s0 + accsq.s1 + accsq.s2 + accsq.s3 + accsq.s4 + accsq.s5 + accsq.s6 + accsq.s7 +
                  accsq.s8 + accsq.s9 + accsq.sA + accsq.sB + accsq.sC + accsq.sD + accsq.sE + accsq.sF;
    barrier(CLK_LOCAL_MEM_FENCE);

    int ls = get_local_size(0);
    for (int s = ls / 2; s > 0; s >>= 1) {
        if (tid < s) {
            sdata[tid] += sdata[tid + s];
            sqdata[tid] += sqdata[tid + s];
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }

    if (tid == 0) {
        sum_out[get_group_id(0)] = sdata[0];
        sumsq_out[get_group_id(0)] = sqdata[0];
    }
}

__kernel void tune_f4_unroll(__global const float *input,
                             __global float *sum_out,
                             __global float *sumsq_out,
                             const int n) {
    __local float sdata[256];
    __local float sqdata[256];
    uint tid = get_local_id(0);
    uint gid = get_global_id(0);
    uint gsize = get_global_size(0);

    float4 acc = (float4)(0.0f, 0.0f, 0.0f, 0.0f);
    float4 accsq = (float4)(0.0f, 0.0f, 0.0f, 0.0f);
    uint n4 = (uint)(n >> 2);
    uint i4 = gid;
    #pragma unroll UNROLL
    for (; i4 < n4; i4 += gsize) {
        float4 v = ((__global const float4 *)input)[i4];
        acc += v;
        accsq += v * v;
    }
    sdata[tid] = acc.x + acc.y + acc.z + acc.w;
    sqdata[tid] = accsq.x + accsq.y + accsq.z + accsq.w;
    barrier(CLK_LOCAL_MEM_FENCE);

    uint ls = (uint)get_local_size(0);
    for (uint s = ls / 2; s > 0; s >>= 1) {
        if (tid < s) {
            sdata[tid] += sdata[tid + s];
            sqdata[tid] += sqdata[tid + s];
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }

    if (tid == 0) {
        sum_out[get_group_id(0)] = sdata[0];
        sumsq_out[get_group_id(0)] = sqdata[0];
    }
}