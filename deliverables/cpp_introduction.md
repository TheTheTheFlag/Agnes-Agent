# C++ 编程语言介绍

## 1. 概述

C++ 是由贝尔实验室的 Bjarne Stroustrup 于 1979 年开始开发的一种通用编程语言，最初名为 "C with Classes"，1983 年正式更名为 C++。C++ 在 C 语言的基础上增加了面向对象特性，同时保留了 C 语言的高效性和灵活性。

C++ 是一门多范式编程语言，支持：
- 过程式编程
- 面向对象编程（OOP）
- 泛型编程（模板）
- 函数式编程（现代 C++）

## 2. 核心特性

### 2.1 面向对象
- 类与对象
- 继承与多态
- 封装与抽象

### 2.2 泛型编程
- 模板（Templates）
- STL（标准模板库）
- 容器、算法、迭代器

### 2.3 内存管理
- 手动内存管理（new/delete）
- RAII（资源获取即初始化）
- 智能指针（unique_ptr, shared_ptr, weak_ptr）

### 2.4 零开销抽象
C++ 的设计哲学是"不为不使用的功能付费"，高级特性在编译期展开，运行时开销与手写代码相当。

## 3. 基本语法示例

```cpp
#include <iostream>
#include <string>
#include <vector>
#include <memory>

using namespace std;

// 类定义
class Animal {
public:
    virtual void speak() const = 0;  // 纯虚函数
    virtual ~Animal() {}
};

class Dog : public Animal {
    string name_;
public:
    Dog(const string& name) : name_(name) {}
    void speak() const override {
        cout << name_ << " says: Woof!" << endl;
    }
};

// 泛型函数
template<typename T>
T maximum(const T& a, const T& b) {
    return (a > b) ? a : b;
}

int main() {
    // 多态示例
    unique_ptr<Animal> dog = make_unique<Dog>("Rex");
    dog->speak();

    // STL 容器
    vector<int> numbers = {1, 3, 5, 2, 4};
    for (int n : numbers) {
        cout << n << " ";
    }
    cout << endl;

    // 泛型函数
    cout << "Max: " << maximum(10, 20) << endl;

    return 0;
}
```

## 4. 主要应用领域

| 领域 | 典型应用 |
|------|----------|
| 系统软件 | 操作系统、编译器、数据库 |
| 游戏开发 | Unreal Engine、Unity 底层 |
| 嵌入式系统 | 汽车、航空航天、物联网 |
| 高性能计算 | 金融交易、搜索引擎、AI 推理 |
| 桌面应用 | Office、Photoshop、浏览器 |

## 5. C++ 版本演进

| 版本 | 发布年份 | 关键特性 |
|------|----------|----------|
| C++98 | 1998 | 首个标准，STL 引入 |
| C++03 | 2003 | 缺陷修正 |
| C++11 | 2011 | 智能指针、auto、lambda、右值引用 |
| C++14 | 2014 | 泛型 lambda、二进制字面量 |
| C++17 | 2017 | 文件系统、optional、if 初始化 |
| C++20 | 2020 | concepts、coroutines、modules、ranges |
| C++23 | 2023 | 模式匹配、static operator() |

## 6. C++ 优缺点

### 优点
- 执行效率高，接近汇编语言
- 灵活的控制权（内存、硬件）
- 丰富的生态系统（STL + 第三方库）
- 跨平台支持
- 向后兼容 C 语言

### 缺点
- 学习曲线陡峭
- 手动内存管理易出错（现代 C++ 已大幅改善）
- 编译时间长
- 语法复杂，容易写出难以维护的代码

## 7. 学习建议

1. **入门**：掌握 C++98/11 基础语法，理解面向对象思想
2. **进阶**：深入学习 STL、模板编程、RAII
3. **现代 C++**：熟练使用 C++11/14/17/20 特性，避免 C 风格代码
4. **实践**：参与开源项目，阅读高质量 C++ 代码

## 8. 参考资源

- 《C++ Primer》—— Stanley B. Lippman
- 《Effective C++》—— Scott Meyers
- C++ Reference：https://en.cppreference.com
- ISO C++ 标准文档

---
*文档生成时间：2026年*
