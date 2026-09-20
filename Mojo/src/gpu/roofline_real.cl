// Experiment A — roofline compute-intensity sweep on a resident buffer.
// Same grid-stride float4 skeleton as reduce_stats_gs (proven 4.9x shape).
// Three variants with escalating ALU-per-byte so the memory-bound→compute-bound
// crossover shows up on the REAL corpus matrix:
//   roofline_l1 — sum + sum-of-squares (memory-bound baseline, ~2 FLOP/elem)
//   roofline_l2 — x^4 + x^2 polynomial (~6 FLOP/elem, more FMA pressure)
//   roofline_l3 — exp(sin(x)) + sqrt(|x|) (transcendentals, ALU-bound; Gen9
//                 FP transcendentals are the slow unit — this is the
//                 deliberately-adversarial intensity)
// Each writes one float per workgroup into sum_out plus (for l1/l2) sumsq.

__kernel void roofline_l1(__global const float *input,
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

    for (int s = 256 / 2; s > 0; s >>= 1) {
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

__kernel void roofline_l2(__global const float *input,
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
        float4 v2 = v * v;
        acc += (v2 * v2);          // x^4
        accsq += v2;               // x^2
    }
    sdata[tid] = acc.x + acc.y + acc.z + acc.w;
    sqdata[tid] = accsq.x + accsq.y + accsq.z + accsq.w;
    barrier(CLK_LOCAL_MEM_FENCE);

    for (int s = 256 / 2; s > 0; s >>= 1) {
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

__kernel void roofline_l3(__global const float *input,
                          __global float *sum_out,
                          __global float *cnt_out,
                          const int n) {
    __local float sdata[256];
    __local float cdata[256];
    int tid = get_local_id(0);
    int gid = get_global_id(0);
    int gsize = get_global_size(0);

    float4 acc = (float4)(0.0f, 0.0f, 0.0f, 0.0f);
    float4 accc = (float4)(0.0f, 0.0f, 0.0f, 0.0f);
    int n4 = n >> 2;
    for (int i4 = gid; i4 < n4; i4 += gsize) {
        float4 v = ((__global const float4 *)input)[i4];
        float4 e = native_exp(native_sin(v));          // -1..e
        float4 r = native_sqrt(fabs(v));               // >= 0
        acc += e;
        accc += r;
    }
    sdata[tid] = acc.x + acc.y + acc.z + acc.w;
    cdata[tid] = accc.x + accc.y + accc.z + accc.w;
    barrier(CLK_LOCAL_MEM_FENCE);

    for (int s = 256 / 2; s > 0; s >>= 1) {
        if (tid < s) {
            sdata[tid] += sdata[tid + s];
            cdata[tid] += cdata[tid + s];
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }

    if (tid == 0) {
        sum_out[get_group_id(0)] = sdata[0];
        cnt_out[get_group_id(0)] = cdata[0];
    }
}