/* Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE. */
import java.io.IOException;
import jakarta.ws.rs.WebApplicationException;
import jakarta.ws.rs.core.Response;
import org.apache.tika.exception.TikaException;
import org.apache.tika.server.core.TikaServerParseException;
import org.apache.tika.server.core.TikaServerParseExceptionMapper;
import org.eclipse.jetty.http.BadMessageException;

/** Client HTTP failures must not disguise unrelated I/O/server failures. */
public final class TikaHttpExceptionProbe {
    private static void check(Exception cause, int expected) {
        Response response = new TikaServerParseExceptionMapper(false).toResponse(new TikaServerParseException(cause));
        if (response.getStatus() != expected) throw new IllegalStateException("Unexpected HTTP error classification");
    }
    public static void main(String[] args) {
        check(new BadMessageException(400, "synthetic framing"), 400);
        check(new IOException(new BadMessageException(413, "synthetic size")), 413);
        check(new TikaException("wrapped", new IOException(new BadMessageException(400, "synthetic"))), 400);
        check(new IOException("synthetic unrelated I/O"), 500);
        check(new BadMessageException(500, "synthetic server failure"), 500);
        check(new WebApplicationException(Response.status(401).build()), 401);
        check(new WebApplicationException(new BadMessageException(400, "nested framing"), 401), 401);
        check(new TikaException("synthetic parser error"), 422);
        System.out.println("HTTP_ERROR_CLASSIFICATION_8_PASS");
    }
}
