package com.example.management.app.state;

import javax.sql.DataSource;
import java.sql.*;
import java.util.*;

/**
 * Database time, row locks and fencing make committed progress portable between RHEL hosts.
 */
public final class JobStore {
  public record Job(String id, String requestId, String name, String status, int totalSteps,
                    int checkpoint, String owner, long epoch) {
  }

  public record Created(Job job, boolean created) {
  }

  public record Claim(String id, String owner, long epoch) {
  }

  private final DataSource dataSource;

  public JobStore(DataSource dataSource) {
    this.dataSource = dataSource;
  }

  public Created create(String key, String name, int steps) throws SQLException {
    if (key == null || key.isBlank() || key.length() > 100 || name == null || name.isBlank()
      || name.length() > 200 || steps < 1 || steps > 300) throw new IllegalArgumentException("Invalid job request");
    String id = UUID.randomUUID().toString();
    try (Connection c = dataSource.getConnection(); PreparedStatement p = c.prepareStatement(
      "INSERT INTO app2_job(id,request_id,name,status,total_steps,checkpoint,epoch,created_at) VALUES(?,?,?,'PENDING',?,0,0,CURRENT_TIMESTAMP)")) {
      p.setString(1, id);
      p.setString(2, key);
      p.setString(3, name);
      p.setInt(4, steps);
      p.setQueryTimeout(5);
      p.executeUpdate();
      return new Created(new Job(id, key, name, "PENDING", steps, 0, null, 0), true);
    } catch (SQLException e) {
      if (e.getErrorCode() != 1 && !"23505".equals(e.getSQLState())) throw e;
      try (Connection c = dataSource.getConnection(); PreparedStatement p = c.prepareStatement("SELECT * FROM app2_job WHERE request_id=?")) {
        p.setString(1, key);
        p.setQueryTimeout(5);
        try (ResultSet rs = p.executeQuery()) {
          if (!rs.next()) throw e;
          Job old = map(rs);
          if (!old.name().equals(name) || old.totalSteps() != steps)
            throw new IllegalArgumentException("requestId already used for different work");
          return new Created(old, false);
        }
      }
    }
  }

  public Optional<Job> find(String id) throws SQLException {
    try (Connection c = dataSource.getConnection(); PreparedStatement p = c.prepareStatement("SELECT * FROM app2_job WHERE id=?")) {
      p.setString(1, id);
      p.setQueryTimeout(5);
      try (ResultSet rs = p.executeQuery()) {
        return rs.next() ? Optional.of(map(rs)) : Optional.empty();
      }
    }
  }

  public Optional<Claim> claim(String owner) throws SQLException {
    try (Connection c = dataSource.getConnection()) {
      c.setAutoCommit(false);
      try (PreparedStatement p = c.prepareStatement("SELECT * FROM app2_job WHERE status='PENDING' OR (status='RUNNING' AND lease_until<CURRENT_TIMESTAMP) ORDER BY created_at FOR UPDATE SKIP LOCKED")) {
        p.setFetchSize(1);
        p.setMaxRows(1);
        p.setQueryTimeout(5);
        String id;
        long epoch;
        try (ResultSet rs = p.executeQuery()) {
          if (!rs.next()) {
            c.rollback();
            return Optional.empty();
          }
          id = rs.getString("id");
          epoch = rs.getLong("epoch") + 1;
        }
        try (PreparedStatement u = c.prepareStatement("UPDATE app2_job SET status='RUNNING',owner=?,epoch=?,lease_until=CURRENT_TIMESTAMP + INTERVAL '30' SECOND WHERE id=?")) {
          u.setString(1, owner);
          u.setLong(2, epoch);
          u.setString(3, id);
          u.setQueryTimeout(5);
          u.executeUpdate();
        }
        c.commit();
        return Optional.of(new Claim(id, owner, epoch));
      } catch (SQLException e) {
        c.rollback();
        throw e;
      }
    }
  }

  /**
   * A stale process cannot commit after its lease expires or another owner claims the job.
   */
  public boolean advance(Claim claim) throws SQLException {
    try (Connection c = dataSource.getConnection(); PreparedStatement p = c.prepareStatement(
      "UPDATE app2_job SET checkpoint=checkpoint+1,status=CASE WHEN checkpoint+1>=total_steps THEN 'SUCCEEDED' ELSE 'RUNNING' END,lease_until=CURRENT_TIMESTAMP + INTERVAL '30' SECOND WHERE id=? AND owner=? AND epoch=? AND status='RUNNING' AND lease_until>CURRENT_TIMESTAMP")) {
      p.setString(1, claim.id());
      p.setString(2, claim.owner());
      p.setLong(3, claim.epoch());
      p.setQueryTimeout(5);
      return p.executeUpdate() == 1;
    }
  }

  private static Job map(ResultSet r) throws SQLException {
    return new Job(r.getString("id"), r.getString("request_id"), r.getString("name"), r.getString("status"), r.getInt("total_steps"), r.getInt("checkpoint"), r.getString("owner"), r.getLong("epoch"));
  }
}
