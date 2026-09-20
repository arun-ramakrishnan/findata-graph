// Dual reduction for the temperature analyzer: per-workgroup partial sum
// and sum-of-squares, so the host can derive mean and stddev.
// Matches the analyzer's Float64 -> Float32 port (Gen9 FP64 is 1/64 rate).

__kernel void reduce_stats(__global const float *input,
                           __global float *sum_out,
                           __global float *sumsq_out,
                           const int n) {
    __local float sdata[256];
    __local float sqdata[256];
    int tid = get_local_id(0);
    int gid = get_global_id(0);
    int group_id = get_group_id(0);
    int block_size = get_local_size(0);

    float v = (gid < n) ? input[gid] : 0.0f;
    sdata[tid] = v;
    sqdata[tid] = v * v;
    barrier(CLK_LOCAL_MEM_FENCE);

    for (int s = block_size / 2; s > 0; s >>= 1) {
        if (tid < s) {
            sdata[tid] += sdata[tid + s];
            sqdata[tid] += sqdata[tid + s];
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }

    if (tid == 0) {
        sum_out[group_id] = sdata[0];
        sumsq_out[group_id] = sqdata[0];
    }
}

// Tuned variant: fixed grid of 4096x256 threads, grid-stride over float4
// chunks. Amortizes thread scheduling and uses 128-bit loads so the kernel
// becomes bandwidth-bound on very large inputs (n must be a multiple of 4).
__kernel void reduce_stats_gs(__global const float *input,
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
