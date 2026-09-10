package com.example.batch;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.Reader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ResourceLoader;
import org.springframework.stereotype.Service;

@Service
class BusinessService {
  record Totals(int records, long expectedCents, long actualCents, int mismatches) { }
  record Result(String id, String app, String businessDate, String status, Totals totals) { }
  private static final Logger log = LoggerFactory.getLogger(BusinessService.class);
  private final ResourceLoader resources;
  private final ObjectMapper json;
  private final String appId;
  private final String inputFile;
  private final Path workDirectory;

  BusinessService(ResourceLoader resources, ObjectMapper json,
                   @Value("${batch.app-id}") String appId,
                   @Value("${batch.input-file}") String inputFile,
                   @Value("${batch.work-directory}") String workDirectory) throws IOException {
    this.resources = resources;
    this.json = json;
    this.appId = appId;
    this.inputFile = inputFile;
    this.workDirectory = Path.of(workDirectory).toAbsolutePath().normalize();
    Files.createDirectories(this.workDirectory);
  }

  Result run(String id, String date) throws IOException {
    Totals totals;
    try (var reader = new InputStreamReader(
      resources.getResource(inputFile).getInputStream(), StandardCharsets.UTF_8)) {
      totals = calculate(reader);
    }
    String status = totals.mismatches() == 0 ? "SUCCEEDED" : "FAILED";
    var result = new Result(id, appId, date, status, totals);

    json.writeValue(workDirectory.resolve(id + ".json").toFile(), result);
    log.info("job_id={} app={} status={} records={}", id, appId, status, totals.records());
    return result;
  }

  static Totals calculate(Reader source) throws IOException {
    var reader = new BufferedReader(source);
    if (!"reference,expected_cents,actual_cents".equals(reader.readLine())) {
      throw new IllegalArgumentException("Invalid CSV header");
    }
    var references = new HashSet<String>();
    int count = 0;
    int mismatches = 0;
    long expected = 0;
    long actual = 0;
    String line;
    while ((line = reader.readLine()) != null) {
      String[] fields = line.split(",", -1);
      if (fields.length != 3 || !fields[0].matches("[A-Za-z0-9_-]+")
        || !references.add(fields[0]) || !fields[1].matches("[0-9]{1,9}")
        || !fields[2].matches("[0-9]{1,9}")) {
        throw new IllegalArgumentException("Invalid or duplicate CSV record at line " + (count + 2));
      }
      long left = Long.parseLong(fields[1]);
      long right = Long.parseLong(fields[2]);
      expected = Math.addExact(expected, left);
      actual = Math.addExact(actual, right);
      if (left != right) {
        mismatches++;
      }
      count++;
    }
    if (count == 0) {
      throw new IllegalArgumentException("Empty CSV input");
    }
    return new Totals(count, expected, actual, mismatches);
  }
}
