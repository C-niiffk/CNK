package com.example.batch;

import org.springframework.util.MultiValueMap;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.LinkedHashMap;
import java.util.Map;

/** Form Verification **/
final class VerifyProcess {
  private VerifyProcess() {}

  static String required(String name) {
    String value = System.getenv(name);
    if (value == null || value.isBlank()) {
      throw new IllegalStateException("Required environment variable " + name);
    }
    return value;
  }

  static String field(Map<String, String> values, String key, String expression) {
    String value = values.getOrDefault(key, "");
    if(value == null || !value.matches(expression)) {
      throw new IllegalArgumentException("Invalid " + key);
    }
    return value;
  }

  static Map<String, String> form(MultiValueMap<String, String> input) {
    Map<String, String> result = new LinkedHashMap<>();
    input.forEach((key, values) -> {
      if(values.size() != 1) {
        throw new IllegalArgumentException("Duplicate field " + key);
      }
      result.put(key, values.getFirst());
    });
    return result;
  }

  static void validateJob(Map<String, String> values) {
    field(values, "app", "app[12]");
    field(values, "type", "SH|API");
    field(values, "name", "reconcile");
    date(values);
  }

  static String date(Map<String, String> values) {
    String value = field(values, "businessDate", "[0-9]{4}-[0-9]{2}-[0-9]{2}");
    try {
      LocalDate.parse(value);
    } catch (DateTimeParseException e) {
      throw new IllegalArgumentException("Invalid businessDate");
    }
    return value;
  }

  static String id(Map<String, String> values) {
    return field(values, "id", "[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}");
  }

  static String shortText(String value) {
    if (value == null) {
      return "";
    }
    return value.length() > 900 ? value.substring(0, 900) + " [truncated]" : value;
  }
}
