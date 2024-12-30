import math

# Hard-coded constants from our derivation:
# A = 44.74
# B = 37.77
# A = -180.85
# B = 78.5
A = -59.53
B = 56.58

def linear_to_log(S):
    """
    Convert a linear value S (0-255) to the log scale L (0-254).
    """
    # Guard against non-positive S.
    if S <= 0:
        return float('-inf')  # or return 0 if preferred
    return round(A + B * math.log(S))

def log_to_linear(L):
    """
    Convert a log scale value L (0-254) back to a linear value S (0-255).
    """
    return round(math.exp((L - A) / B))

if __name__ == "__main__":
    # Demo: check the two known points
    print("L at S=128:", linear_to_log(128))  # should be ~228
    print("L at S=255:", linear_to_log(255))  # should be ~254

    print("S at L=228:", log_to_linear(228))  # should be ~128
    print("S at L=254:", log_to_linear(254))  # should be ~255

    # Example usage
    test_S = 200
    L_value = linear_to_log(test_S)
    round_trip_S = log_to_linear(L_value)
    print(f"S={test_S} -> L={L_value:.2f} -> S={round_trip_S:.2f}")

    for i in range(0,255,5):
        print(f"{i}:   {linear_to_log(i)}    {log_to_linear(i)}   {log_to_linear(linear_to_log(i))}   {linear_to_log(log_to_linear(i))}")

        
    # 
    #     print(f"{i}:   {brightness_to_arc(i)}    {arc_to_brightness(i)}")

