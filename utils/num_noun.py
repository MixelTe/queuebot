def num_noun(num: int, one: str, two: str, five: str):
    num = abs(num)
    num %= 100
    if num >= 5 and num <= 20:
        return five
    num %= 10
    if num == 1:
        return one
    if num >= 2 and num <= 4:
        return two
    return five
