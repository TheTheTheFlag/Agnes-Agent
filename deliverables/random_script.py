def generate_random():
    """派蒙的随机函数"""
    import random
    return random.randint(1, 100)

if __name__ == "__main__":
    print(f"随机数: {generate_random()}")