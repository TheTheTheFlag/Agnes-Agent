# Java 17 新特性实战：从语法糖到工程化升级

> **作者**：Java 技术分享小组
> **适用版本**：JDK 17（Java 11 之后的下一个 LTS 长期支持版本，2021 年 9 月发布）
> **阅读时长**：约 18 分钟
> **难度**：中级
> **配套大纲**：`deliverables/java-tech-sharing-blog-outline.md`

---

## 📑 目录

1. [引言：你和 Java 17 之间只差这篇文章](#一引言你和-java-17-之间只差这篇文章)
2. [核心特性总览](#二核心特性总览)
3. [Record：消灭模板代码的"数据载体"](#三record消灭模板代码的数据载体)
4. [Sealed Classes：收口继承的"白名单"](#四sealed-classes收口继承的白名单)
5. [Pattern Matching for switch：消灭 if-else 类型链](#五pattern-matching-for-switch消灭-if-else-类型链)
6. [Text Blocks：写 SQL / JSON / HTML 不再拼接](#六text-blocks写-sql--json--html-不再拼接)
7. [Stream & 集合 API 加料](#七stream--集合-api-加料)
8. [工程化升级：从"能跑"到"敢上生产"](#八工程化升级从能跑到敢上生产)
9. [实战对比：一个完整业务场景改造](#九实战对比一个完整业务场景改造)
10. [总结与可执行清单](#十总结与可执行清单)
11. [常见问题 FAQ](#十一常见问题-faq)
12. [附录：环境搭建与运行示例](#附录环境搭建与运行示例)

---

## 一、引言：你和 Java 17 之间只差这篇文章

如果留意身边的技术招聘 JD，会发现一个明显趋势：**Spring Boot 3.x、Jakarta EE 9+、Elasticsearch 8.x、Kafka 3.x 都已经把"最低 Java 版本"钉在了 17**。换句话说，无论你愿不愿意，Java 17 已经是当下 Java 后端的"事实底线"。

但真正让人纠结的并不是"要不要升"，而是：

- 听过很多新特性，**但没在项目里用过**，怕踩坑；
- JEP 列表动辄十几项，**不知道哪些值得先上**；
- 想用 Pattern Matching，又担心**预览特性**不敢上生产；
- 团队代码里大量 POJO/DTO，**Record 到底能替掉多少**。

本文精选 **5 个最值得立刻用起来**的 Java 17 特性，配以"可贴即用"的代码示例、迁移路径和工程化建议。读完后，你应当能回答两个问题：

1. **我该不该升 Java 17？** —— 该，而且优先级高于任何业务新需求。
2. **我该先上哪些特性？** —— Text Blocks、`Stream.toList()`、Record 零风险先行；Sealed + Switch Pattern Matching 评估后启用。

> 文中所有代码均可在 JDK 17 上编译运行；带 `--enable-preview` 标注的为预览特性，请勿直接用于生产。

---

## 二、核心特性总览

| 编号 | 特性 | 引入版本 | 解决问题 | 实战价值 | 风险 |
|------|------|----------|----------|----------|------|
| 1 | **Record** | 16（17 推荐） | POJO 模板代码 | ⭐⭐⭐⭐⭐ | 极低 |
| 2 | **Sealed Classes** | 17 | 继承体系失控 | ⭐⭐⭐⭐ | 低 |
| 3 | **Pattern Matching for switch** | 17（预览） | 类型分支繁琐 | ⭐⭐⭐⭐ | 中（需 `--enable-preview`） |
| 4 | **Text Blocks** | 15（17 稳定） | 多行字符串拼接 | ⭐⭐⭐⭐⭐ | 极低 |
| 5 | **Stream / 集合 API 升级** | 16-17 | `Collectors.toList()` 等样板 | ⭐⭐⭐ | 极低 |

> **配套工程化**：强封装（Strong Encapsulation）下反射的 `--add-opens` 兜底、CI 切换 Temurin 17、IntelliJ IDEA 2023+ 支持。

下面进入正题。

---

## 三、Record：消灭模板代码的"数据载体"

### 3.1 痛点回顾

在 Java 8 时代，我们写一个用于接口传输的 `UserDTO` 通常要 50 行代码：字段、私有化、构造器、`getter`、`equals`、`hashCode`、`toString`，缺一不可。如果你用 Lombok，至少也得写 5 个注解。Lombok 之外的方案里，要么忍，要么用 IDE 自动生成——而**自动生成恰恰是问题**：字段一改，相关方法全部要重生成。

### 3.2 Record 是什么

[JEP 395](https://openjdk.org/jeps/395) 引入的 `record` 是一种**专为"数据载体"设计的类**。编译器会**自动生成**构造器、访问器（注意不是 `getXxx()`，而是 `x()`）、`equals`、`hashCode`、`toString`，且整个类隐式 `final`。

```java
// Java 17：仅一行
public record Point(int x, int y) { }
```

### 3.3 EX-01：UserDTO 前后对比

**Java 8 时代（精简前还有 40+ 行）**：

```java
public final class UserDTO {
    private Long id;
    private String name;
    private String email;
    private LocalDateTime createdAt;

    public UserDTO(Long id, String name, String email, LocalDateTime createdAt) {
        this.id = id;
        this.name = name;
        this.email = email;
        this.createdAt = createdAt;
    }

    public Long getId() { return id; }
    public String getName() { return name; }
    public String getEmail() { return email; }
    public LocalDateTime getCreatedAt() { return createdAt; }

    @Override public boolean equals(Object o) { /* 20 行 */ return false; }
    @Override public int hashCode() { /* 5 行 */ return 0; }
    @Override public String toString() { /* 1 行 */ return ""; }
    // 还有 50 行模板代码...
}
```

**Java 17 时代**：

```java
public record UserDTO(
    Long id,
    String name,
    String email,
    LocalDateTime createdAt
) { }
```

**输出验证**：

```java
var u = new UserDTO(1L, "Tom", "tom@example.com", LocalDateTime.now());
System.out.println(u.name());         // Tom        （注意是 name() 不是 getName()）
System.out.println(u);               // UserDTO[id=1, name=Tom, email=tom@example.com, ...]
System.out.println(u.equals(new UserDTO(1L, "Tom", "tom@example.com", LocalDateTime.now()))); // true
```

> **重点**：Record 的访问器叫 `name()` 而非 `getName()`。这一点是和 Lombok / Bean 规范最大的差异，也是和 Jackson、MyBatis 等框架集成时最容易踩坑的地方。

### 3.4 EX-02：紧凑构造器做校验

如果想在创建对象时做合法性校验，可以用**紧凑构造器（Compact Constructor）**——只写参数，不写赋值：

```java
public record Email(String value) {
    public Email {  // 紧凑构造器：没有参数列表
        if (value == null || !value.contains("@")) {
            throw new IllegalArgumentException("invalid email: " + value);
        }
    }
}

// 使用
var ok  = new Email("a@b.com");   // OK
// var bad = new Email("nope");   // 抛 IllegalArgumentException
```

紧凑构造器在你"只是想校验"而不需要修改字段时非常顺手；如果需要新增字段，再写完整构造器即可。

### 3.5 进阶用法

- **实现接口**：`record implements Serializable {}` 完全允许。
- **泛型**：`record Page<T>(List<T> data, int total) {}`。
- **静态字段**：可以加 `static` 成员，但不能加实例字段（除 record header 声明的）。
- **与 Sealed 配合**：见第 4 章，`sealed interface Shape permits Circle, Square {}` 里的 `Circle` 通常就是 record。

### 3.6 使用边界

| 场景 | Record 适用？ | 说明 |
|------|---------------|------|
| DTO / VO / 命令对象 | ✅ | 最常见 |
| 不可变值对象（Money、Range） | ✅ | 完美匹配 |
| JPA / MyBatis 实体类 | ⚠️ | 框架依赖无参构造 + setter，需用 `@Component` + `@JsonCreator` 兜底 |
| 需要继承的业务基类 | ❌ | Record 隐式 final，不能被继承 |
| Spring `@ConfigurationProperties` | ✅ | 17 + Spring Boot 3 完美支持 |

---

## 四、Sealed Classes：收口继承的"白名单"

### 4.1 痛点回顾

假设有一个 `Result<T>` 表示"操作结果"，可能是 `Success<T>` 也可能是 `Failure`：

```java
// Java 8 风格
public class Result<T> { /* ... */ }
public class Success<T> extends Result<T> { /* ... */ }
public class Failure extends Result<Empty> { /* ... */ }

// 半年后，同事又加了 Pending、Cancelled、TimedOut...
```

带来的问题：

- 任何人都能在任何包 `extends Result`；
- 编译器**无法知道** `Result` 的全部子类；
- `switch (result) { ... }` 必须加 `default`，否则新增子类时**编译器不报错**——埋雷。

### 4.2 Sealed 的语法

[JEP 409](https://openjdk.org/jeps/409) 引入的 `sealed` 关键字让你**显式列出"许可继承"的白名单**：

```java
public sealed class Result<T> permits Success, Failure {
    // ...
}

public final class Success<T> extends Result<T> {
    private final T value;
    public Success(T value) { this.value = value; }
    public T value() { return value; }
}

public final class Failure extends Result<Empty> {
    private final String error;
    public Failure(String error) { this.error = error; }
    public String error() { return error; }
}
```

现在任何想 `extends Result` 的类**必须**是 `Success` 或 `Failure` 之一；编译器会替你把关。

### 4.3 三种"许可方式"

| 修饰符 | 语义 | 何时用 |
|--------|------|--------|
| `final`（子类） | 不允许再被继承 | 99% 的场景 |
| `sealed`（子类） | 自己也开白名单 | 形成多级封闭继承树 |
| `non-sealed`（子类） | 恢复开放继承 | 需要扩展点（如 `Plugin`） |

### 4.4 EX-03：Shape 体系完整示例

```java
// 顶层：sealed 接口
public sealed interface Shape permits Circle, Square, Triangle {
    double area();
}

// 子类 1：record + final（隐式）
public record Circle(double radius) implements Shape {
    @Override public double area() { return Math.PI * radius * radius; }
}

// 子类 2：record
public record Square(double side) implements Shape {
    @Override public double area() { return side * side; }
}

// 子类 3：传统 final 类（也可以）
public final class Triangle implements Shape {
    private final double base, height;
    public Triangle(double base, double height) {
        this.base = base; this.height = height;
    }
    @Override public double area() { return 0.5 * base * height; }
}
```

> **最佳实践**：Sealed 的子类**强烈推荐用 record**，写起来又少 50 行。

### 4.5 与枚举 / Record 的取舍

| 场景 | 用什么 |
|------|--------|
| 有限且每种只一份（如星期、订单状态） | `enum` |
| 有限但每种带不同数据（如 `Success<T>` vs `Failure`） | `sealed` + `record` 子类 |
| 可能有几十种且需要扩展 | `interface` + `default` 方法 |
| 完全封闭、且不同子类无独立数据 | `sealed` + `final` 子类 |

---

## 五、Pattern Matching for switch：消灭 if-else 类型链

> ⚠️ **本节特性在 Java 17 仍为预览特性**，编译和运行必须加 `--enable-preview`。
> 在 Java 21 中已转正（[JEP 441](https://openjdk.org/jeps/441)）。

### 5.1 起点：instanceof 模式匹配（Java 16 已转正）

Java 16 引入的 `instanceof` 模式匹配让"判断类型 + 强转"合二为一：

```java
// Java 8
if (obj instanceof String) {
    String s = (String) obj;
    System.out.println(s.length());
}

// Java 16+
if (obj instanceof String s) {
    System.out.println(s.length());  // s 已被强转，作用域仅限 if 块
}
```

### 5.2 switch 模式匹配让"多类型分发"一目了然

```java
static String describe(Object obj) {
    return switch (obj) {
        case Integer i -> "整数 " + i;
        case Long l    -> "长整数 " + l;
        case String s  -> "字符串长度=" + s.length();
        case null      -> "空值";
        default        -> "其他类型: " + obj.getClass().getSimpleName();
    };
}
```

### 5.3 穷尽性检查（Exhaustiveness）

当 `switch` 的目标类型是 `sealed`，**编译器能验证你是否覆盖了所有子类**。这就是 Sealed + Switch Pattern Matching 的"化学反应"。

### 5.4 EX-04：重构 Shape 分发

```java
static String shapeName(Shape shape) {
    return switch (shape) {
        case Circle c    -> "圆形 r=" + c.radius();
        case Square s    -> "正方形 边=" + s.side();
        case Triangle t  -> "三角形";   // 不写 default 也能编译！
        // 如果你新加一个 Rectangle，编译器会立刻报错：未覆盖所有 sealed 子类
    };
}
```

### 5.5 EX-05：处理包装类型与 null

```java
static int boxedToInt(Object o) {
    return switch (o) {
        case Integer i -> i;
        case Long    l -> l.intValue();
        case null     -> 0;     // Java 17 必须显式处理 null
        default       -> throw new IllegalArgumentException("不支持: " + o);
    };
}
```

> **注意**：Java 17 中 `null` 必须显式分支，否则编译失败；Java 21 进一步放宽。

### 5.6 编译与运行

```bash
javac --enable-preview --release 17 SwitchDemo.java
java  --enable-preview SwitchDemo
```

---

## 六、Text Blocks：写 SQL / JSON / HTML 不再拼接

[JEP 378](https://openjdk.org/jeps/378) 引入的文本块是 Java 15 的正式特性，Java 17 完全稳定。

### 6.1 痛点：反斜杠地狱

```java
// Java 8：拼接多行 SQL
String sql = "SELECT id, name, email\n"
           + "FROM users\n"
           + "WHERE created_at > ?\n"
           + "  AND status = 'ACTIVE'\n"
           + "ORDER BY id DESC\n"
           + "LIMIT 10";
```

### 6.2 文本块语法

三个双引号开，三个双引号关。**缩进按"最左边非空字符"为基准自动去除**。

```java
String sql = """
        SELECT id, name, email
        FROM users
        WHERE created_at > ?
          AND status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 10
        """;
```

### 6.3 关键规则

| 规则 | 说明 |
|------|------|
| 缩进 | 编译时移除每行共同的**前导空白** |
| 行尾空格 | 自动 trim，但需保留时用 `\s` |
| 换行 | 末尾自动加 `\n`（除非行末是 `\`） |
| 转义 | `\"` 仍表示一个 `"`；`\\` 表示一个 `\` |
| 插值 | **不支持** `${var}`，需用 `formatted()` |

### 6.4 EX-06：写 SQL

```java
String sql = """
        SELECT id, name, email
        FROM users
        WHERE created_at > ?
        """;
PreparedStatement ps = conn.prepareStatement(sql);
ps.setTimestamp(1, Timestamp.from(Instant.now().minusSeconds(86400)));
```

### 6.5 EX-07：构造 JSON（用 `formatted()` 做简单插值）

```java
String name = "Tom";
int age = 28;
String json = """
        {
          "name": "%s",
          "age": %d
        }
        """.formatted(name, age);
System.out.println(json);
// {
//   "name": "Tom",
//   "age": 28
// }
```

> **注意**：`%s` 在用户输入含 `"` 时会有注入风险。生产环境请用 Jackson `ObjectMapper.writeValueAsString()` 或 `String.replace()` 转义。

### 6.6 与模板引擎的边界

- **文本块适合**：简单 SQL、JSON 草稿、HTML 邮件模板、Prometheus 规则。
- **仍需模板引擎**：循环、条件判断、国际化、片段复用 → 继续用 Thymeleaf / Freemarker / Pebble。

---

## 七、Stream & 集合 API 加料

这一节是"零风险零成本"的微升级，立刻就能在 Java 17 项目里用。

### 7.1 EX-08：一组小升级合集

```java
public class ApiUpgrades {
    public static void main(String[] args) {
        // 1. Stream.toList() —— 告别 Collectors.toList()
        List<String> names = Stream.of("Tom", "Jerry", "Spike")
                                   .filter(s -> s.length() > 2)
                                   .toList();   // 返回不可变 List<E>
        // System.out.println(names.getClass()); // class java.util.ImmutableCollections$ListN

        // 2. List.copyOf() —— 创建不可变副本
        List<String> copied = List.copyOf(names);
        // copied.add("Tyke"); // UnsupportedOperationException

        // 3. Map.ofEntries() —— 超过 10 个 key-value pair 的最佳搭档
        Map<String, Integer> map = Map.ofEntries(
            Map.entry("a", 1),
            Map.entry("b", 2),
            Map.entry("c", 3)
        );

        // 4. RandomGenerator 体系 —— 替代老的 java.util.Random
        RandomGenerator rng = RandomGenerator.of("L64X128MixRandom");
        int[] arr = rng.ints(5, 1, 100).toArray();
        System.out.println(Arrays.toString(arr));  // [42, 87, 15, 63, 91]

        // 5. 十六进制格式化
        byte[] bytes = {0x1A, 0x2B, (byte) 0xFF};
        String hex = "%04x".formatted(0xCAFE);  // "cafe"
    }
}
```

### 7.2 重要变化

- **`Stream.toList()` 返回的是不可变 List**，比 `Collectors.toList()`（可变）更安全，但不能 `.add()`。
- **`RandomGenerator`** 提供 5 种算法： `L32X64MixRandom`（默认）、`L64X128MixRandom`、`L128X256MixRandom`、`XorWow`、`SplittableRandom` 等，可按场景选。
- **`Map.ofEntries`** 解决了 `Map.of()` 只能传 10 个 KV 的限制。

---

## 八、工程化升级：从"能跑"到"敢上生产"

特性学完了，更难的是"升上去不出事"。这一节给一份可执行的工程化清单。

### 8.1 升级路径

#### 8.1.1 工具链对齐

```xml
<!-- pom.xml -->
<properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <maven.compiler.release>17</maven.compiler.release>
</properties>
```

```groovy
// build.gradle
java {
    toolchain { languageVersion = JavaLanguageVersion.of(17) }
}
```

#### 8.1.2 第三方库兼容矩阵（2024 年现状）

| 库 | 最低 Java 版本 | 说明 |
|----|----------------|------|
| Spring Boot 3.x | 17 | 强制 17 |
| Spring Boot 2.7.x | 8 | 仍可用，但 EOL |
| Elasticsearch 8.x | 17 | 客户端 |
| Kafka 3.7+ | 8 | 客户端可选 17 |
| MyBatis 3.5+ | 8 | 兼容 17 |
| Lombok 1.18.30+ | 8 | 兼容 17 |

> 升级前先去各库官网查兼容矩阵，**不要赌运气**。

#### 8.1.3 EX-09：强封装下反射的 `--add-opens` 兜底

Java 17 默认**强封装** JDK 内部 API，过去的 `--add-opens` 反射可能在升级后报 `InaccessibleObjectException`。常见报错与对策：

```bash
# Spring/Hibernate 用到的反射
java --add-opens java.base/java.lang=ALL-UNNAMED \
     --add-opens java.base/java.lang.reflect=ALL-UNNAMED \
     --add-opens java.base/java.util=ALL-UNNAMED \
     --add-opens java.base/java.io=ALL-UNNAMED \
     -jar myapp.jar
```

**生产建议**：把参数写进 `JAVA_OPTS` 环境变量；用 JFR 记录一次启动过程，看哪些模块真的需要 open。

### 8.2 IDE 与 CI

- **IntelliJ IDEA 2023.2+** 对 Record、Sealed、Pattern Matching 都有完整语法高亮与重构。
- **GitHub Actions 示例**：

```yaml
- name: Set up JDK 17
  uses: actions/setup-java@v4
  with:
    distribution: temurin
    java-version: '17'
```

- **GitLab CI 示例**：

```yaml
image: eclipse-temurin:17-jdk
```

### 8.3 渐进式启用策略

不要一次性"全部上"，按风险分层：

| 阶段 | 特性 | 风险 | 建议时机 |
|------|------|------|----------|
| 阶段 1（第 1 天） | Text Blocks、`Stream.toList()`、`Map.ofEntries` | 极低 | 立即 |
| 阶段 2（第 1 周） | Record | 低 | 重构 POJO 时自然引入 |
| 阶段 3（第 2 周） | Sealed | 低 | 设计新模块时 |
| 阶段 4（评估后） | Switch Pattern Matching | 中 | 等 Java 21 转正更稳 |

### 8.4 监控 & 兜底

升级前后用 Java Flight Recorder（JFR）记录一次"基线"：

```bash
jfr start name=baseline settings=profile duration=60s filename=upgrade.jfr
```

对比 GC 暂停、线程数、启动时间，确认升级没有回退。

---

## 九、实战对比：一个完整业务场景改造

> 场景：订单服务需要根据支付结果返回不同"视图"给前端。

### 9.1 改造前（Java 8 风格）

```java
public class PaymentResult {
    private boolean success;
    private String orderId;
    private String errorCode;
    private String errorMsg;
    private Long paidAt;
    // 100+ 行 getter/setter/equals/hashCode...
}

public String render(PaymentResult r) {
    if (r.isSuccess()) {
        return "支付成功 orderId=" + r.getOrderId() + "  paidAt=" + r.getPaidAt();
    } else {
        return "支付失败 code=" + r.getErrorCode() + " msg=" + r.getErrorMsg();
    }
}
```

**问题**：`PaymentResult` 字段多、`render` 还要 `if-else`、将来加 `Refunded` 状态会大改。

### 9.2 改造后（Java 17）

```java
// 1. 用 sealed 收口
public sealed interface PaymentResult permits Success, Failure {
    String orderId();
}
public record Success(String orderId, long paidAt) implements PaymentResult {}
public record Failure(String orderId, String errorCode, String errorMsg) implements PaymentResult {}

// 2. 用 switch 模式匹配渲染
public String render(PaymentResult r) {
    return switch (r) {
        case Success s -> "支付成功 orderId=" + s.orderId() + "  paidAt=" + s.paidAt();
        case Failure f -> "支付失败 code=" + f.errorCode() + " msg=" + f.errorMsg();
    };
    // 注意：必须 --enable-preview
}
```

**收益**：

- 一个文件，**50 行 vs 150 行**；
- 加 `Refunded` 子类时，**编译器立刻在 switch 处提示未覆盖**；
- Record 自动生成 `equals/hashCode`，做单元测试断言更轻松。

---

## 十、总结与可执行清单

### 10.1 一句话总结

**Java 17 是一次"工程化升级"而非"语法炫技"**：5 个特性可分两批落地——Text Blocks、Stream 小升级、Record 立即用；Sealed + Switch Pattern Matching 评估后用。

### 10.2 落地清单（3 步走）

**第 1 步：零风险升级（建议当天完成）**

- [ ] 团队 JDK 切到 Temurin 17
- [ ] CI 流水线 JDK 版本对齐
- [ ] 项目里搜 `Collectors.toList()`，批量替换为 `.toList()`
- [ ] 多行字符串拼接改用 Text Blocks
- [ ] 第三方库过一遍兼容矩阵

**第 2 步：可控重构（1-2 周）**

- [ ] 识别"纯数据载体"的类（DTO/VO/命令对象），改用 Record
- [ ] Jackson 用 `@JsonCreator` 兜底无参构造
- [ ] 业务模块设计时考虑 `sealed interface` 收口

**第 3 步：长期演进（1-3 月）**

- [ ] 评估 Switch Pattern Matching（建议等 Java 21 转正后）
- [ ] 计划升级到 Java 21，拿下 Virtual Threads
- [ ] 用 JFR 对比升级前后性能

### 10.3 升级心法

> **先看工具链、再看语言特性、最后看 JVM 参数。**
> 90% 的升级事故都出在"工具链没对齐"上，特性本身反而最稳。

### 10.4 系列预告

下一篇将写 **《Java 21 虚拟线程实战：10 倍并发不是梦》**，覆盖：

- Virtual Threads 原理（M:N 调度）
- 同步代码 vs 异步代码的取舍
- `StructuredTaskScope` 并发编排
- 4 个真实业务场景改造

---

## 十一、常见问题 FAQ

**Q1：Java 17 和 Java 11 比，到底强在哪？**
A：LTS 与 LTS 之间隔了 6 年（2018-2021），攒了 14 个 JEP。语言层最大变化是 Record、Sealed、Pattern Matching；API 层多了 `Stream.toList()`、`RandomGenerator`、`HttpClient`（标准化）等。

**Q2：我们项目还在 Java 8，能直接跳到 17 吗？**
A：可以，但分两步：先升到 Java 11（兼容性最稳），再用半年到 17。一步到位风险高。

**Q3：Record 会被 Lombok 替代吗？**
A：会逐步替代。Lombok 适合"老代码补全"；Record 是 Java 官方方案，新项目首选。

**Q4：Switch Pattern Matching 在生产能用吗？**
A：Java 17 中是预览特性，**不建议上生产**。Java 21 已转正，是更稳妥的版本。

**Q5：升级 Java 17 后反射报错怎么办？**
A：八成是 `--add-opens` 没加。用第 8.1.3 节的命令，把需要的模块一一加白。

**Q6：商业 JDK（Oracle JDK）收费吗？**
A：Oracle JDK 17+ 对生产环境**按用户/处理器收费**。免费方案推荐 Eclipse Temurin、Azul Zulu、Microsoft OpenJDK 三家 LTS。

**Q7：和 Kotlin / Scala 比，Java 17 优势？**
A：生态最广、招聘最容易、性能稳定、学习曲线平缓。新特性（Record/Sealed/Pattern Matching）大部分借鉴自 Kotlin / Scala，但**保持 Java 的稳**。

---

## 附录：环境搭建与运行示例

### A.1 安装 JDK 17

```bash
# macOS (Homebrew)
brew install --cask temurin@17

# Ubuntu
sudo apt install temurin-17-jdk

# Windows (Chocolatey)
choco install temurin17
```

### A.2 编译运行（无预览特性）

```bash
javac --release 17 Demo.java
java Demo
```

### A.3 编译运行（带预览特性，如 Switch Pattern Matching）

```bash
javac --enable-preview --release 17 SwitchDemo.java
java  --enable-preview SwitchDemo
```

### A.4 IDE 推荐设置

- **IntelliJ IDEA**：File → Project Structure → Project SDK = 17；Language Level = 17。
- **VS Code**：安装 Extension Pack for Java，设置 `java.configuration.runtimes`。
- **Eclipse**：安装 2023-09+ 版本，Target Platform 切到 Java 17。

### A.5 参考资料

- [JEP 395: Records](https://openjdk.org/jeps/395)
- [JEP 409: Sealed Classes](https://openjdk.org/jeps/409)
- [JEP 406: Pattern Matching for switch (Preview)](https://openjdk.org/jeps/406)
- [JEP 420: Pattern Matching for switch (Second Preview)](https://openjdk.org/jeps/420)
- [JEP 378: Text Blocks](https://openjdk.org/jeps/378)
- [JEP 356: Enhanced Pseudo-Random Number Generators](https://openjdk.org/jeps/356)
- [JEP 403: Strongly Encapsulate JDK Internals](https://openjdk.org/jeps/403)
- [Eclipse Temurin 下载](https://adoptium.net/)

---

> **写在最后**：Java 17 不是一个"等别人踩完坑我再上"的版本，而是一个**当下主流生态的硬性要求**。早升早享受，升完别忘了回头把团队里 100+ 行的 POJO 用 Record 重构一次——那种"一口气删 80 行"的感觉，是这个版本最爽的部分。
