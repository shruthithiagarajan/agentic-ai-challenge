import sys
d = int(sys.argv[1])
if d <= 5:
    print("0 apology only")
elif d <= 10:
    print("3 written apology + 3% credit")
elif d <= 20:
    print("5 written apology + 5% credit + expedited freight")
else:
    print("8 written apology + 8% credit + right to cancel")
