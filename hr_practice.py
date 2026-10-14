"""Virtu/SIG等量化Hackerrank在线编程筛 · 代表题型练习册
风格说明: 纯算法题, 通常1-2题/60-90分钟, 要求正确性+复杂度+边界处理。
本文件每题: 题目/思路/代码/复杂度, 底部assert全部通过。跑: python hr_practice.py
"""
from collections import Counter, deque

# ---------- 1. 两数之和 (哈希表 O(n)) ----------
def two_sum(nums, target):
    seen = {}
    for i, v in enumerate(nums):
        rem = target - v
        if rem in seen:
            return [seen[rem], i]
        seen[v] = i
    return []

# ---------- 2. 变位词分组 (排序键/计数键 O(nk)) ----------
def group_anagrams(words):
    d = {}
    for w in words:
        key = tuple(sorted(w))
        d.setdefault(key, []).append(w)
    return [sorted(v) for v in d.values()]

# ---------- 3. 最大子数组和 (Kadane O(n)) ----------
def max_subarray_sum(a):
    best = cur = a[0]
    for x in a[1:]:
        cur = max(x, cur + x)
        best = max(best, cur)
    return best

# ---------- 4. 除自身以外数组的乘积 (O(n), 不除法) ----------
def product_except_self(a):
    n = len(a)
    out = [1] * n
    left = 1
    for i in range(n):
        out[i] = left
        left *= a[i]
    right = 1
    for i in range(n - 1, -1, -1):
        out[i] *= right
        right *= a[i]
    return out

# ---------- 5. 括号匹配 (栈 O(n)) ----------
def is_balanced(s):
    st = []
    pair = {')': '(', ']': '[', '}': '{'}
    for ch in s:
        if ch in '([{':
            st.append(ch)
        elif ch in pair:
            if not st or st.pop() != pair[ch]:
                return False
    return not st

# ---------- 6. 数字位计数: 1..n 里关于某约束的计数 (模拟) ----------
def count_evens_prefix(a):
    """每个位置前缀里偶数的个数。"""
    out = []
    c = 0
    for x in a:
        if x % 2 == 0:
            c += 1
        out.append(c)
    return out

# ---------- 7. 两个有序数组合并 (双指针 O(n+m)) ----------
def merge_sorted(a, b):
    i = j = 0
    out = []
    while i < len(a) and j < len(b):
        if a[i] < b[j]:
            out.append(a[i]); i += 1
        else:
            out.append(b[j]); j += 1
    out.extend(a[i:]); out.extend(b[j:])
    return out

# ---------- 8. 第一个不重复字符 (Counter O(n)) ----------
def first_unique(s):
    cnt = Counter(s)
    for i, ch in enumerate(s):
        if cnt[ch] == 1:
            return i
    return -1

# ---------- 9. DP: 爬楼梯方式数 (状态优化 O(n)) ----------
def climb_stairs(n):
    a, b = 1, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b

# ---------- 10. 位运算: 二进制里1的个数 (EE轨迹常见) ----------
def count_bits(n):
    c = 0
    while n:
        n &= n - 1
        c += 1
    return c

# ---------- 11. 概率/期望: n次独立事件, 抛硬币至少k次正面的概率 ----------
def prob_at_least(n, k, p=0.5):
    """二项分布P(X>=k), 精确小数(小额n够用)。"""
    from math import comb
    s = 0.0
    for i in range(k, n + 1):
        s += comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    return round(s, 8)

# ---------- 12. 滑动窗口: 长度k子数组的最大平均值 (O(n)) ----------
def max_avg_window(a, k):
    cur = sum(a[:k])
    best = cur / k
    for i in range(k, len(a)):
        cur += a[i] - a[i - k]
        best = max(best, cur / k)
    return round(best, 6)

# ---------- 13. 链表/栈: 数组中下一个更大元素 (单调栈 O(n)) ----------
def next_greater(a):
    out = [-1] * len(a)
    st = []
    for i in range(len(a) - 1, -1, -1):
        while st and a[st[-1]] <= a[i]:
            st.pop()
        if st:
            out[i] = a[st[-1]]
        st.append(i)
    return out

# ---------- 14. 迷宫最短步数(BFS) ----------
def min_steps(grid, start, end):
    """grid: 0空地 1墙; 四方向; 返回最少步数。"""
    rows, cols = len(grid), len(grid[0])
    q = deque([(start[0], start[1], 0)])
    seen = {start}
    while q:
        r, c, d = q.popleft()
        if (r, c) == end:
            return d
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 0 \
               and (nr, nc) not in seen:
                seen.add((nr, nc))
                q.append((nr, nc, d + 1))
    return -1


if __name__ == "__main__":
    assert two_sum([2, 7, 11, 15], 9) == [0, 1]
    assert two_sum([3, 2, 4], 6) == [1, 2]
    assert sorted(map(sorted, group_anagrams(["eat", "tea", "tan", "ate", "nat", "bat"]))) == \
        sorted([["ate", "eat", "tea"], ["bat"], ["nat", "tan"]])
    assert max_subarray_sum([-2, 1, -3, 4, -1, 2, 1, -5, 4]) == 6
    assert product_except_self([1, 2, 3, 4]) == [24, 12, 8, 6]
    assert is_balanced("({[]})") and not is_balanced("({[)]}")
    assert count_evens_prefix([1, 2, 3, 4, 6]) == [0, 1, 1, 2, 3]
    assert merge_sorted([1, 3, 5], [2, 4, 6]) == [1, 2, 3, 4, 5, 6]
    assert first_unique("loveleetcode") == 2
    assert climb_stairs(5) == 8
    assert count_bits(0b101101) == 4
    assert abs(prob_at_least(10, 6) - 0.376953125) < 1e-8
    assert max_avg_window([1, 12, -5, -6, 50, 3], 4) == 12.75
    assert next_greater([4, 5, 2, 25]) == [5, 25, 25, -1]
    assert min_steps([[0, 0, 0], [0, 1, 0], [0, 0, 0]], (0, 0), (2, 2)) == 4
    assert min_steps([[0, 1, 0], [0, 1, 0], [0, 1, 0]], (0, 0), (2, 2)) == -1
    print("ALL 14 PASS · 每题复杂度见注释 · 刷完就能打Y查漏补缺")