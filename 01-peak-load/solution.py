"""Моя реализация поиска пиковой нагрузки"""


import sys


def find_peak(requests):
    changes = {}

    for start, end, cost in requests:
        changes[start] = changes.get(start, 0) + cost
        changes[end] = changes.get(end, 0) - cost

    current_load = 0
    max_load = 0
    earliest_time = 0

    for time in sorted(changes):
        current_load += changes[time]

        if current_load > max_load:
            max_load = current_load
            earliest_time = time

    return max_load, earliest_time


def main():
    input = sys.stdin.buffer.readline

    n = int(input())
    requests = []

    for _ in range(n):
        start, end, cost = map(int, input().split())
        requests.append((start, end, cost))

    max_load, earliest_time = find_peak(requests)
    print(max_load, earliest_time)


if __name__ == "__main__":
    main()
