/*
 * eglfix.c — libEGL.so.1 interposer for Factorio 2.1.x native-Wayland crash.
 *
 * Problem: at first shader compile, eglGetCurrentContext() == NULL on the
 * main thread even though the context was bound successfully during init.
 * Every GL call then fails silently (glCreateShader -> 0) and Factorio
 * aborts with "Failed to create shader".
 *
 * This shim is loaded via LD_PRELOAD and declares SONAME libEGL.so.1, so
 * any dlopen("libEGL.so.1") by the game or SDL returns THIS library. We
 * forward every EGL call to the real libEGL, remember the last successful
 * eglMakeCurrent binding, and right before the game's first
 * glCreateShader/glCreateProgram call, if the calling thread has no current
 * context, we re-bind the remembered (display, surface, context).
 *
 * Build:
 *   gcc -shared -fPIC -O2 -o libEGL.so.1 eglfix.c -ldl -lpthread \
 *       -Wl,-soname,libEGL.so.1
 */
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* Minimal GL types (EGL headers do not pull these in) */
typedef unsigned int GLenum;
typedef unsigned int GLuint;
typedef int GLsizei;
typedef int GLint;
typedef unsigned int GLbitfield;
typedef long GLsizeiptr;
typedef long GLintptr;
typedef char GLchar;
typedef EGLBoolean(*real_eglMakeCurrent_t)(EGLDisplay, EGLSurface, EGLSurface, EGLContext);
typedef EGLContext(*real_eglGetCurrentContext_t)(void);
typedef EGLint(*real_eglGetError_t)(void);
typedef GLuint(*real_glCreateShader_t)(GLenum);
typedef GLuint(*real_glCreateProgram_t)(void);
typedef __eglMustCastToProperFunctionPointerType(*real_eglGetProcAddress_t)(const char *);

/* forward decls used by the gl* wrappers below */
static void fx_ensure_before_gl(const char *what);
static unsigned int fx_check_gl_after(const char *what, void *retaddr);

/* ------------------------------------------------------------------ */
/* logging                                                             */
/* ------------------------------------------------------------------ */
static double fx_now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

static void fx_log(const char *fmt, ...) {
    FILE *f = fopen("/tmp/eglfix.log", "a");
    if (!f) return;
    fprintf(f, "[eglfix t=%ld %.1fms] ", (long)gettid(), fx_now_ms());
    va_list ap;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

/* ------------------------------------------------------------------ */
/* real library resolution                                             */
/* ------------------------------------------------------------------ */
static void *real_lib = NULL;

static void *sym(const char *name) {
    return dlsym(real_lib, name);
}

/* real_eglGetProcAddress returns __eglMustCastToProperFunctionPointerType;
 * use a helper that casts to void* for our own use. */
static void *sym_proc(const char *name) {
    real_eglGetProcAddress_t r = (real_eglGetProcAddress_t)sym("eglGetProcAddress");
    if (!r) return NULL;
    return (void *)r(name);
}

static void resolve(void) {
    if (real_lib) return;
    /* Prefer the real glvnd dispatcher. dlopen by ABSOLUTE PATH so we don't
     * get ourselves back (we match by SONAME, not by path). */
    real_lib = dlopen("/usr/lib/libEGL.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!real_lib) real_lib = dlopen("/usr/lib64/libEGL.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!real_lib) {
        /* fall back to the Mesa vendor lib (exports the full EGL API) */
        real_lib = dlopen("libEGL_mesa.so.0", RTLD_NOW | RTLD_LOCAL);
    }
    if (!real_lib) {
        fx_log("FATAL: could not dlopen real libEGL: %s", dlerror());
        return;
    }
    /* Guard: if we somehow got ourselves, bail loudly. */
    void *probe = sym("eglGetProcAddress");
    if (probe == (void *)&eglGetProcAddress) {
        fx_log("FATAL: dlopen returned OUR shim (recursion). dlerror=%s", dlerror());
        real_lib = NULL;
        return;
    }
    fx_log("real libEGL loaded: %p", real_lib);
}

/* ------------------------------------------------------------------ */
/* remembered binding                                                  */
/* ------------------------------------------------------------------ */
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static EGLDisplay g_disp = EGL_NO_DISPLAY;
static EGLContext g_ctx = EGL_NO_CONTEXT;
static EGLSurface g_draw = EGL_NO_SURFACE;
static EGLSurface g_read = EGL_NO_SURFACE;
static int g_surface_alive = 0; /* recorded draw surface not yet destroyed */
static int g_ensured = 0;       /* one-shot: auto-rebind already done for current binding */

static real_eglMakeCurrent_t R_eglMakeCurrent = NULL;
static real_eglGetCurrentContext_t R_eglGetCurrentContext = NULL;
static real_eglGetError_t R_eglGetError = NULL;

static void ensure_current(void) {
    resolve();
    if (!R_eglMakeCurrent || !R_eglGetCurrentContext) {
        R_eglMakeCurrent = (real_eglMakeCurrent_t)sym("eglMakeCurrent");
        R_eglGetCurrentContext = (real_eglGetCurrentContext_t)sym("eglGetCurrentContext");
        R_eglGetError = (real_eglGetError_t)sym("eglGetError");
    }
    if (!R_eglMakeCurrent || !g_ctx) return;

    if (R_eglGetCurrentContext() != EGL_NO_CONTEXT) return; /* already current */

    pthread_mutex_lock(&g_lock);
    EGLDisplay d = g_disp;
    EGLContext c = g_ctx;
    EGLSurface s = g_surface_alive ? g_draw : EGL_NO_SURFACE;
    int done = g_ensured;
    pthread_mutex_unlock(&g_lock);

    if (done || !d || !c) return;
    EGLBoolean ok = R_eglMakeCurrent(d, s, s, c);
    EGLint err = R_eglGetError();
    fx_log("ensure_current: rebind d=%p s=%p c=%p -> %d err=0x%x", d, s, c, ok, err);
    if (ok != EGL_TRUE && s != EGL_NO_SURFACE) {
        EGLBoolean ok2 = R_eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c);
        fx_log("ensure_current: surfaceless retry -> %d err=0x%x", ok2, R_eglGetError());
    }
    if (ok == EGL_TRUE || s == EGL_NO_SURFACE) {
        /* one-shot: only auto-rebind once per recorded binding; a new
         * binding/context (eglMakeCurrent/eglCreateContext) resets this. */
        pthread_mutex_lock(&g_lock);
        g_ensured = 1;
        pthread_mutex_unlock(&g_lock);
    }
}

/* ------------------------------------------------------------------ */
/* wrapped GL entry points (the first GL calls after context loss)     */
/* ------------------------------------------------------------------ */
static real_glCreateShader_t R_glCreateShader = NULL;
static real_glCreateProgram_t R_glCreateProgram = NULL;

GLuint glCreateShader(GLenum type) {
    resolve();
    if (!R_glCreateShader) R_glCreateShader = (real_glCreateShader_t)sym_proc("glCreateShader");
    ensure_current();
    if (!R_glCreateShader) return 0;
    return R_glCreateShader(type);
}

GLuint glCreateProgram(void) {
    resolve();
    if (!R_glCreateProgram) R_glCreateProgram = (real_glCreateProgram_t)sym_proc("glCreateProgram");
    ensure_current();
    if (!R_glCreateProgram) return 0;
    return R_glCreateProgram();
}

/* ------------------------------------------------------------------ */
/* glGetError + texture-call wrappers (diagnostics + ensure)           */
/* ------------------------------------------------------------------ */
typedef unsigned int (*real_glGetError_t)(void);
static real_glGetError_t R_glGetErrorGL = NULL;

typedef void (*real_glGenTextures_t)(GLsizei, unsigned int *);
static real_glGenTextures_t R_glGenTextures = NULL;
typedef void (*real_glBindTexture_t)(GLenum, unsigned int);
static real_glBindTexture_t R_glBindTexture = NULL;
typedef void (*real_glTexImage2D_t)(GLenum, int, int, GLsizei, GLsizei, int, GLenum, GLenum, const void *);
static real_glTexImage2D_t R_glTexImage2D = NULL;
typedef void (*real_glGenBuffers_t)(GLsizei, unsigned int *);
static real_glGenBuffers_t R_glGenBuffers = NULL;
typedef void (*real_glBufferData_t)(GLenum, long, const void *, GLenum);
static real_glBufferData_t R_glBufferData = NULL;
typedef void (*real_glClear_t)(GLenum);
static real_glClear_t R_glClear = NULL;
typedef EGLBoolean(*real_eglQuerySurface_t)(EGLDisplay, EGLSurface, EGLint, EGLint *);
static real_eglQuerySurface_t R_eglQuerySurface = NULL;
typedef void (*real_glDrawArrays_t)(GLenum, int, GLsizei);
static real_glDrawArrays_t R_glDrawArrays = NULL;
typedef void (*real_glDrawElements_t)(GLenum, GLsizei, GLenum, const void *);
static real_glDrawElements_t R_glDrawElements = NULL;
typedef void (*real_glUseProgram_t)(unsigned int);
static real_glUseProgram_t R_glUseProgram = NULL;
typedef void (*real_glBindVertexArray_t)(unsigned int);
static real_glBindVertexArray_t R_glBindVertexArray = NULL;
typedef void (*real_glEnable_t)(GLenum);
static real_glEnable_t R_glEnable = NULL;
typedef void (*real_glDisable_t)(GLenum);
static real_glDisable_t R_glDisable = NULL;

void glDrawArrays(GLenum mode, int first, GLsizei count) {
    resolve();
    if (!R_glDrawArrays) R_glDrawArrays = (real_glDrawArrays_t)sym_proc("glDrawArrays");
    fx_ensure_before_gl("glDrawArrays");
    if (R_glDrawArrays) R_glDrawArrays(mode, first, count);
    fx_check_gl_after("glDrawArrays", __builtin_return_address(0));
}

void glDrawElements(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    resolve();
    if (!R_glDrawElements) R_glDrawElements = (real_glDrawElements_t)sym_proc("glDrawElements");
    fx_ensure_before_gl("glDrawElements");
    if (R_glDrawElements) R_glDrawElements(mode, count, type, indices);
    fx_check_gl_after("glDrawElements", __builtin_return_address(0));
}

void glUseProgram(unsigned int program) {
    resolve();
    if (!R_glUseProgram) R_glUseProgram = (real_glUseProgram_t)sym_proc("glUseProgram");
    fx_ensure_before_gl("glUseProgram");
    if (R_glUseProgram) R_glUseProgram(program);
    fx_check_gl_after("glUseProgram", __builtin_return_address(0));
}

void glBindVertexArray(unsigned int array) {
    resolve();
    if (!R_glBindVertexArray) R_glBindVertexArray = (real_glBindVertexArray_t)sym_proc("glBindVertexArray");
    fx_ensure_before_gl("glBindVertexArray");
    if (R_glBindVertexArray) R_glBindVertexArray(array);
    fx_check_gl_after("glBindVertexArray", __builtin_return_address(0));
}

void glEnable(GLenum cap) {
    resolve();
    if (!R_glEnable) R_glEnable = (real_glEnable_t)sym_proc("glEnable");
    fx_ensure_before_gl("glEnable");
    if (R_glEnable) R_glEnable(cap);
    fx_check_gl_after("glEnable", __builtin_return_address(0));
}

typedef void (*real_glViewport_t)(int, int, GLsizei, GLsizei);
static real_glViewport_t R_glViewport = NULL;
typedef void (*real_glTexSubImage2D_t)(GLenum, int, int, int, GLsizei, GLsizei, GLenum, GLenum, const void *);
static real_glTexSubImage2D_t R_glTexSubImage2D = NULL;
typedef void (*real_glCompressedTexImage2D_t)(GLenum, int, GLenum, GLsizei, GLsizei, int, GLsizei, const void *);
static real_glCompressedTexImage2D_t R_glCompressedTexImage2D = NULL;
typedef void (*real_glTexStorage2D_t)(GLenum, int, GLenum, GLsizei, GLsizei);
static real_glTexStorage2D_t R_glTexStorage2D = NULL;
typedef void (*real_glGenerateMipmap_t)(GLenum);
static real_glGenerateMipmap_t R_glGenerateMipmap = NULL;
typedef void (*real_glPixelStorei_t)(GLenum, int);
static real_glPixelStorei_t R_glPixelStorei = NULL;

typedef void (*real_glCreateTextures_t)(GLenum, GLsizei, unsigned int *);
static real_glCreateTextures_t R_glCreateTextures = NULL;
typedef void (*real_glTextureStorage2D_t)(unsigned int, int, GLenum, GLsizei, GLsizei);
static real_glTextureStorage2D_t R_glTextureStorage2D = NULL;
typedef void (*real_glTextureSubImage2D_t)(unsigned int, int, int, int, GLsizei, GLsizei, GLenum, GLenum, const void *);
static real_glTextureSubImage2D_t R_glTextureSubImage2D = NULL;
typedef void (*real_glBindFramebuffer_t)(GLenum, unsigned int);
static real_glBindFramebuffer_t R_glBindFramebuffer = NULL;
typedef void (*real_glFramebufferTexture2D_t)(GLenum, GLenum, GLenum, unsigned int, int);
static real_glFramebufferTexture2D_t R_glFramebufferTexture2D = NULL;

void glViewport(int x, int y, GLsizei width, GLsizei height) {
    resolve();
    if (!R_glViewport) R_glViewport = (real_glViewport_t)sym_proc("glViewport");
    fx_ensure_before_gl("glViewport");
    if (R_glViewport) R_glViewport(x, y, width, height);
}

void glTexSubImage2D(GLenum target, int level, int xoffset, int yoffset, GLsizei width, GLsizei height, GLenum format, GLenum type, const void *pixels) {
    resolve();
    if (!R_glTexSubImage2D) R_glTexSubImage2D = (real_glTexSubImage2D_t)sym_proc("glTexSubImage2D");
    fx_ensure_before_gl("glTexSubImage2D");
    if (R_glTexSubImage2D) R_glTexSubImage2D(target, level, xoffset, yoffset, width, height, format, type, pixels);
    fx_check_gl_after("glTexSubImage2D", __builtin_return_address(0));
}

void glCompressedTexImage2D(GLenum target, int level, GLenum internalformat, GLsizei width, GLsizei height, int border, GLsizei imageSize, const void *data) {
    resolve();
    if (!R_glCompressedTexImage2D) R_glCompressedTexImage2D = (real_glCompressedTexImage2D_t)sym_proc("glCompressedTexImage2D");
    fx_ensure_before_gl("glCompressedTexImage2D");
    if (R_glCompressedTexImage2D) R_glCompressedTexImage2D(target, level, internalformat, width, height, border, imageSize, data);
    fx_check_gl_after("glCompressedTexImage2D", __builtin_return_address(0));
}

void glTexStorage2D(GLenum target, int levels, GLenum internalformat, GLsizei width, GLsizei height) {
    resolve();
    if (!R_glTexStorage2D) R_glTexStorage2D = (real_glTexStorage2D_t)sym_proc("glTexStorage2D");
    fx_ensure_before_gl("glTexStorage2D");
    if (R_glTexStorage2D) R_glTexStorage2D(target, levels, internalformat, width, height);
    fx_check_gl_after("glTexStorage2D", __builtin_return_address(0));
}

void glGenerateMipmap(GLenum target) {
    resolve();
    if (!R_glGenerateMipmap) R_glGenerateMipmap = (real_glGenerateMipmap_t)sym_proc("glGenerateMipmap");
    fx_ensure_before_gl("glGenerateMipmap");
    if (R_glGenerateMipmap) R_glGenerateMipmap(target);
    fx_check_gl_after("glGenerateMipmap", __builtin_return_address(0));
}

void glPixelStorei(GLenum pname, int param) {
    resolve();
    if (!R_glPixelStorei) R_glPixelStorei = (real_glPixelStorei_t)sym_proc("glPixelStorei");
    fx_ensure_before_gl("glPixelStorei");
    if (R_glPixelStorei) R_glPixelStorei(pname, param);
    fx_check_gl_after("glPixelStorei", __builtin_return_address(0));
}

void glCreateTextures(GLenum target, GLsizei n, unsigned int *textures) {
    resolve();
    if (!R_glCreateTextures) R_glCreateTextures = (real_glCreateTextures_t)sym_proc("glCreateTextures");
    fx_ensure_before_gl("glCreateTextures");
    if (R_glCreateTextures) R_glCreateTextures(target, n, textures);
    fx_check_gl_after("glCreateTextures", __builtin_return_address(0));
}

void glTextureStorage2D(unsigned int texture, int levels, GLenum internalformat, GLsizei width, GLsizei height) {
    resolve();
    if (!R_glTextureStorage2D) R_glTextureStorage2D = (real_glTextureStorage2D_t)sym_proc("glTextureStorage2D");
    fx_ensure_before_gl("glTextureStorage2D");
    if (R_glTextureStorage2D) R_glTextureStorage2D(texture, levels, internalformat, width, height);
    fx_check_gl_after("glTextureStorage2D", __builtin_return_address(0));
}

void glTextureSubImage2D(unsigned int texture, int level, int xoffset, int yoffset, GLsizei width, GLsizei height, GLenum format, GLenum type, const void *pixels) {
    resolve();
    if (!R_glTextureSubImage2D) R_glTextureSubImage2D = (real_glTextureSubImage2D_t)sym_proc("glTextureSubImage2D");
    fx_ensure_before_gl("glTextureSubImage2D");
    if (R_glTextureSubImage2D) R_glTextureSubImage2D(texture, level, xoffset, yoffset, width, height, format, type, pixels);
    fx_check_gl_after("glTextureSubImage2D", __builtin_return_address(0));
}

void glBindFramebuffer(GLenum target, unsigned int framebuffer) {
    resolve();
    if (!R_glBindFramebuffer) R_glBindFramebuffer = (real_glBindFramebuffer_t)sym_proc("glBindFramebuffer");
    fx_ensure_before_gl("glBindFramebuffer");
    if (R_glBindFramebuffer) R_glBindFramebuffer(target, framebuffer);
    fx_check_gl_after("glBindFramebuffer", __builtin_return_address(0));
}

void glFramebufferTexture2D(GLenum target, GLenum attachment, GLenum textarget, unsigned int texture, int level) {
    resolve();
    if (!R_glFramebufferTexture2D) R_glFramebufferTexture2D = (real_glFramebufferTexture2D_t)sym_proc("glFramebufferTexture2D");
    fx_ensure_before_gl("glFramebufferTexture2D");
    if (R_glFramebufferTexture2D) R_glFramebufferTexture2D(target, attachment, textarget, texture, level);
    fx_check_gl_after("glFramebufferTexture2D", __builtin_return_address(0));
}

void glDisable(GLenum cap) {
    resolve();
    if (!R_glDisable) R_glDisable = (real_glDisable_t)sym_proc("glDisable");
    fx_ensure_before_gl("glDisable");
    if (R_glDisable) R_glDisable(cap);
    fx_check_gl_after("glDisable", __builtin_return_address(0));
}

static void fx_ensure_before_gl(const char *what) {
    ensure_current();
    if (R_eglGetCurrentContext && R_eglGetCurrentContext() == EGL_NO_CONTEXT) {
        fx_log("WARN %s called with NO current context on this thread", what);
    }
}

static unsigned int fx_check_gl_after(const char *what, void *retaddr) {
    if (!R_glGetErrorGL) return 0;
    unsigned int e = R_glGetErrorGL();
    if (e != 0) {
        Dl_info dli;
        const char *sym = "?";
        if (dladdr(retaddr, &dli) && dli.dli_sname) sym = dli.dli_sname;
        fx_log("SET-ERROR %s -> 0x%x (caller=%s)", what, e, sym);
    }
    return e;
}

unsigned int glGetError(void) {
    resolve();
    if (!R_glGetErrorGL) R_glGetErrorGL = (real_glGetError_t)sym_proc("glGetError");
    if (!R_glGetErrorGL) return 0;
    return R_glGetErrorGL();
}

void glGenTextures(GLsizei n, unsigned int *textures) {
    resolve();
    if (!R_glGenTextures) R_glGenTextures = (real_glGenTextures_t)sym_proc("glGenTextures");
    fx_ensure_before_gl("glGenTextures");
    if (R_glGenTextures) R_glGenTextures(n, textures);
    fx_check_gl_after("glGenTextures", __builtin_return_address(0));
}

void glBindTexture(GLenum target, unsigned int texture) {
    resolve();
    if (!R_glBindTexture) R_glBindTexture = (real_glBindTexture_t)sym_proc("glBindTexture");
    fx_ensure_before_gl("glBindTexture");
    if (R_glBindTexture) R_glBindTexture(target, texture);
    fx_check_gl_after("glBindTexture", __builtin_return_address(0));
}

void glTexImage2D(GLenum target, int level, int internalformat, GLsizei width, GLsizei height, int border, GLenum format, GLenum type, const void *pixels) {
    resolve();
    if (!R_glTexImage2D) R_glTexImage2D = (real_glTexImage2D_t)sym_proc("glTexImage2D");
    fx_ensure_before_gl("glTexImage2D");
    if (R_glTexImage2D) R_glTexImage2D(target, level, internalformat, width, height, border, format, type, pixels);
    fx_check_gl_after("glTexImage2D", __builtin_return_address(0));
}

void glGenBuffers(GLsizei n, unsigned int *buffers) {
    resolve();
    if (!R_glGenBuffers) R_glGenBuffers = (real_glGenBuffers_t)sym_proc("glGenBuffers");
    fx_ensure_before_gl("glGenBuffers");
    if (R_glGenBuffers) R_glGenBuffers(n, buffers);
    fx_check_gl_after("glGenBuffers", __builtin_return_address(0));
}

void glBufferData(GLenum target, long size, const void *data, GLenum usage) {
    resolve();
    if (!R_glBufferData) R_glBufferData = (real_glBufferData_t)sym_proc("glBufferData");
    fx_ensure_before_gl("glBufferData");
    if (R_glBufferData) R_glBufferData(target, size, data, usage);
    fx_check_gl_after("glBufferData", __builtin_return_address(0));
}

void glClear(GLenum mask) {
    resolve();
    if (!R_glClear) R_glClear = (real_glClear_t)sym_proc("glClear");
    fx_ensure_before_gl("glClear");
    if (R_glClear) R_glClear(mask);
    fx_check_gl_after("glClear", __builtin_return_address(0));
}

/* ------------------------------------------------------------------ */
/* eglGetProcAddress: hand out wrappers for the shader-entry functions */
/* ------------------------------------------------------------------ */
static real_eglGetProcAddress_t R_eglGetProcAddress = NULL;

__eglMustCastToProperFunctionPointerType eglGetProcAddress(const char *name) {
    resolve();
    if (!R_eglGetProcAddress) R_eglGetProcAddress = (real_eglGetProcAddress_t)sym("eglGetProcAddress");
    if (!R_eglGetProcAddress || !name) return NULL;
    __eglMustCastToProperFunctionPointerType r = R_eglGetProcAddress(name);
    if (!r) return NULL;
    if (!strcmp(name, "glCreateShader")) return (__eglMustCastToProperFunctionPointerType)&glCreateShader;
    if (!strcmp(name, "glCreateProgram")) return (__eglMustCastToProperFunctionPointerType)&glCreateProgram;
    if (!strcmp(name, "glGetError")) return (__eglMustCastToProperFunctionPointerType)&glGetError;
    if (!strcmp(name, "glGenTextures")) return (__eglMustCastToProperFunctionPointerType)&glGenTextures;
    if (!strcmp(name, "glBindTexture")) return (__eglMustCastToProperFunctionPointerType)&glBindTexture;
    if (!strcmp(name, "glTexImage2D")) return (__eglMustCastToProperFunctionPointerType)&glTexImage2D;
    if (!strcmp(name, "glGenBuffers")) return (__eglMustCastToProperFunctionPointerType)&glGenBuffers;
    if (!strcmp(name, "glBufferData")) return (__eglMustCastToProperFunctionPointerType)&glBufferData;
    if (!strcmp(name, "glClear")) return (__eglMustCastToProperFunctionPointerType)&glClear;
    if (!strcmp(name, "glDrawArrays")) return (__eglMustCastToProperFunctionPointerType)&glDrawArrays;
    if (!strcmp(name, "glDrawElements")) return (__eglMustCastToProperFunctionPointerType)&glDrawElements;
    if (!strcmp(name, "glUseProgram")) return (__eglMustCastToProperFunctionPointerType)&glUseProgram;
    if (!strcmp(name, "glBindVertexArray")) return (__eglMustCastToProperFunctionPointerType)&glBindVertexArray;
    if (!strcmp(name, "glEnable")) return (__eglMustCastToProperFunctionPointerType)&glEnable;
    if (!strcmp(name, "glDisable")) return (__eglMustCastToProperFunctionPointerType)&glDisable;
    if (!strcmp(name, "glViewport")) return (__eglMustCastToProperFunctionPointerType)&glViewport;
    if (!strcmp(name, "glTexSubImage2D")) return (__eglMustCastToProperFunctionPointerType)&glTexSubImage2D;
    if (!strcmp(name, "glCompressedTexImage2D")) return (__eglMustCastToProperFunctionPointerType)&glCompressedTexImage2D;
    if (!strcmp(name, "glTexStorage2D")) return (__eglMustCastToProperFunctionPointerType)&glTexStorage2D;
    if (!strcmp(name, "glGenerateMipmap")) return (__eglMustCastToProperFunctionPointerType)&glGenerateMipmap;
    if (!strcmp(name, "glPixelStorei")) return (__eglMustCastToProperFunctionPointerType)&glPixelStorei;
    if (!strcmp(name, "glCreateTextures")) return (__eglMustCastToProperFunctionPointerType)&glCreateTextures;
    if (!strcmp(name, "glTextureStorage2D")) return (__eglMustCastToProperFunctionPointerType)&glTextureStorage2D;
    if (!strcmp(name, "glTextureSubImage2D")) return (__eglMustCastToProperFunctionPointerType)&glTextureSubImage2D;
    if (!strcmp(name, "glBindFramebuffer")) return (__eglMustCastToProperFunctionPointerType)&glBindFramebuffer;
    if (!strcmp(name, "glFramebufferTexture2D")) return (__eglMustCastToProperFunctionPointerType)&glFramebufferTexture2D;
    return r;
}

/* ------------------------------------------------------------------ */
/* eglMakeCurrent: forward + remember                                  */
/* ------------------------------------------------------------------ */
EGLBoolean eglMakeCurrent(EGLDisplay d, EGLSurface draw, EGLSurface read, EGLContext ctx) {
    resolve();
    if (!R_eglMakeCurrent) R_eglMakeCurrent = (real_eglMakeCurrent_t)sym("eglMakeCurrent");
    if (!R_eglMakeCurrent) return EGL_FALSE;
    EGLBoolean r = R_eglMakeCurrent(d, draw, read, ctx);
    if (r == EGL_TRUE) {
        pthread_mutex_lock(&g_lock);
        if (ctx != EGL_NO_CONTEXT) {
            EGLint w = -1, h = -1;
            if (draw != EGL_NO_SURFACE && R_eglQuerySurface) {
                R_eglQuerySurface(d, draw, EGL_WIDTH, &w);
                R_eglQuerySurface(d, draw, EGL_HEIGHT, &h);
            }
            g_disp = d;
            g_draw = draw;
            g_read = read;
            g_ctx = ctx;
            g_surface_alive = (draw != EGL_NO_SURFACE);
            g_ensured = 0; /* new binding -> allow one auto-rebind if needed */
            fx_log("bind recorded d=%p draw=%p read=%p ctx=%p surfsize=%dx%d", d, draw, read, ctx, w, h);
        } else {
            fx_log("unbind (ctx=NULL) on d=%p — keeping last good binding for restore", d);
        }
        pthread_mutex_unlock(&g_lock);
    } else {
        fx_log("eglMakeCurrent FAILED d=%p draw=%p ctx=%p err=0x%x",
               d, draw, ctx, R_eglGetError ? R_eglGetError() : 0);
    }
    return r;
}

/* ------------------------------------------------------------------ */
/* other EGL calls: log + forward                                      */
/* ------------------------------------------------------------------ */
static void *(*R_eglGetDisplay)(EGLNativeDisplayType) = NULL;
static void *(*R_eglGetPlatformDisplay)(EGLenum, void *, const EGLAttrib *) = NULL;
static void *(*R_eglGetPlatformDisplayEXT)(EGLenum, void *, const EGLint *) = NULL;

EGLDisplay eglGetDisplay(EGLNativeDisplayType display_id) {
    resolve();
    if (!R_eglGetDisplay) R_eglGetDisplay = (void *(*)(EGLNativeDisplayType))sym("eglGetDisplay");
    return R_eglGetDisplay ? R_eglGetDisplay(display_id) : EGL_NO_DISPLAY;
}

EGLDisplay eglGetPlatformDisplay(EGLenum platform, void *native_display, const EGLAttrib *attrib_list) {
    resolve();
    if (!R_eglGetPlatformDisplay) R_eglGetPlatformDisplay = (void *(*)(EGLenum, void *, const EGLAttrib *))sym("eglGetPlatformDisplay");
    return R_eglGetPlatformDisplay ? R_eglGetPlatformDisplay(platform, native_display, attrib_list) : EGL_NO_DISPLAY;
}

EGLDisplay eglGetPlatformDisplayEXT(EGLenum platform, void *native_display, const EGLint *attrib_list) {
    resolve();
    if (!R_eglGetPlatformDisplayEXT) R_eglGetPlatformDisplayEXT = (void *(*)(EGLenum, void *, const EGLint *))sym("eglGetPlatformDisplayEXT");
    return R_eglGetPlatformDisplayEXT ? R_eglGetPlatformDisplayEXT(platform, native_display, attrib_list) : EGL_NO_DISPLAY;
}

EGLBoolean eglInitialize(EGLDisplay dpy, EGLint *major, EGLint *minor) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLint *, EGLint *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLint *, EGLint *))sym("eglInitialize");
    return f ? f(dpy, major, minor) : EGL_FALSE;
}

EGLBoolean eglTerminate(EGLDisplay dpy) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay))sym("eglTerminate");
    return f ? f(dpy) : EGL_FALSE;
}

const char *eglQueryString(EGLDisplay dpy, EGLint name) {
    resolve();
    static const char *(*f)(EGLDisplay, EGLint) = NULL;
    if (!f) f = (const char *(*)(EGLDisplay, EGLint))sym("eglQueryString");
    return f ? f(dpy, name) : NULL;
}

EGLBoolean eglGetConfigs(EGLDisplay dpy, EGLConfig *configs, EGLint config_size, EGLint *num_config) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLConfig *, EGLint, EGLint *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLConfig *, EGLint, EGLint *))sym("eglGetConfigs");
    return f ? f(dpy, configs, config_size, num_config) : EGL_FALSE;
}

EGLBoolean eglChooseConfig(EGLDisplay dpy, const EGLint *attrib_list, EGLConfig *configs, EGLint config_size, EGLint *num_config) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, const EGLint *, EGLConfig *, EGLint, EGLint *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, const EGLint *, EGLConfig *, EGLint, EGLint *))sym("eglChooseConfig");
    return f ? f(dpy, attrib_list, configs, config_size, num_config) : EGL_FALSE;
}

EGLBoolean eglGetConfigAttrib(EGLDisplay dpy, EGLConfig config, EGLint attribute, EGLint *value) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLConfig, EGLint, EGLint *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLConfig, EGLint, EGLint *))sym("eglGetConfigAttrib");
    return f ? f(dpy, config, attribute, value) : EGL_FALSE;
}

static EGLSurface (*R_eglCreateWindowSurface)(EGLDisplay, EGLConfig, EGLNativeWindowType, const EGLint *) = NULL;
static void *(*R_eglCreatePlatformWindowSurface)(EGLDisplay, EGLConfig, void *, const EGLAttrib *) = NULL;
static void *(*R_eglCreatePlatformWindowSurfaceEXT)(EGLDisplay, EGLConfig, void *, const EGLint *) = NULL;

EGLSurface eglCreateWindowSurface(EGLDisplay dpy, EGLConfig config, EGLNativeWindowType win, const EGLint *attrib_list) {
    resolve();
    if (!R_eglCreateWindowSurface) R_eglCreateWindowSurface = (EGLSurface (*)(EGLDisplay, EGLConfig, EGLNativeWindowType, const EGLint *))sym("eglCreateWindowSurface");
    return R_eglCreateWindowSurface ? R_eglCreateWindowSurface(dpy, config, win, attrib_list) : EGL_NO_SURFACE;
}

EGLSurface eglCreatePlatformWindowSurface(EGLDisplay dpy, EGLConfig config, void *native_window, const EGLAttrib *attrib_list) {
    resolve();
    if (!R_eglCreatePlatformWindowSurface) R_eglCreatePlatformWindowSurface = (void *(*)(EGLDisplay, EGLConfig, void *, const EGLAttrib *))sym("eglCreatePlatformWindowSurface");
    return R_eglCreatePlatformWindowSurface ? R_eglCreatePlatformWindowSurface(dpy, config, native_window, attrib_list) : EGL_NO_SURFACE;
}

EGLSurface eglCreatePlatformWindowSurfaceEXT(EGLDisplay dpy, EGLConfig config, void *native_window, const EGLint *attrib_list) {
    resolve();
    if (!R_eglCreatePlatformWindowSurfaceEXT) R_eglCreatePlatformWindowSurfaceEXT = (void *(*)(EGLDisplay, EGLConfig, void *, const EGLint *))sym("eglCreatePlatformWindowSurfaceEXT");
    return R_eglCreatePlatformWindowSurfaceEXT ? R_eglCreatePlatformWindowSurfaceEXT(dpy, config, native_window, attrib_list) : EGL_NO_SURFACE;
}

EGLSurface eglCreatePbufferSurface(EGLDisplay dpy, EGLConfig config, const EGLint *attrib_list) {
    resolve();
    static EGLSurface (*f)(EGLDisplay, EGLConfig, const EGLint *) = NULL;
    if (!f) f = (EGLSurface (*)(EGLDisplay, EGLConfig, const EGLint *))sym("eglCreatePbufferSurface");
    return f ? f(dpy, config, attrib_list) : EGL_NO_SURFACE;
}

EGLBoolean eglDestroySurface(EGLDisplay dpy, EGLSurface surface) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSurface) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface))sym("eglDestroySurface");
    pthread_mutex_lock(&g_lock);
    if (surface == g_draw || surface == g_read) {
        g_surface_alive = 0;
        fx_log("eglDestroySurface: recorded surface %p destroyed (d=%p)", surface, dpy);
    }
    pthread_mutex_unlock(&g_lock);
    return f ? f(dpy, surface) : EGL_FALSE;
}

EGLBoolean eglQuerySurface(EGLDisplay dpy, EGLSurface surface, EGLint attribute, EGLint *value) {
    resolve();
    if (!R_eglQuerySurface) R_eglQuerySurface = (real_eglQuerySurface_t)sym("eglQuerySurface");
    return R_eglQuerySurface ? R_eglQuerySurface(dpy, surface, attribute, value) : EGL_FALSE;
}

EGLBoolean eglSurfaceAttrib(EGLDisplay dpy, EGLSurface surface, EGLint attribute, EGLint value) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSurface, EGLint, EGLint) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface, EGLint, EGLint))sym("eglSurfaceAttrib");
    return f ? f(dpy, surface, attribute, value) : EGL_FALSE;
}

EGLBoolean eglBindTexImage(EGLDisplay dpy, EGLSurface surface, EGLint buffer) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSurface, EGLint) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface, EGLint))sym("eglBindTexImage");
    return f ? f(dpy, surface, buffer) : EGL_FALSE;
}

EGLBoolean eglReleaseTexImage(EGLDisplay dpy, EGLSurface surface, EGLint buffer) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSurface, EGLint) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface, EGLint))sym("eglReleaseTexImage");
    return f ? f(dpy, surface, buffer) : EGL_FALSE;
}

EGLBoolean eglSwapInterval(EGLDisplay dpy, EGLint interval) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLint) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLint))sym("eglSwapInterval");
    return f ? f(dpy, interval) : EGL_FALSE;
}

EGLContext eglCreateContext(EGLDisplay dpy, EGLConfig config, EGLContext share_context, const EGLint *attrib_list) {
    resolve();
    static EGLContext (*f)(EGLDisplay, EGLConfig, EGLContext, const EGLint *) = NULL;
    if (!f) f = (EGLContext (*)(EGLDisplay, EGLConfig, EGLContext, const EGLint *))sym("eglCreateContext");
    EGLContext c = f ? f(dpy, config, share_context, attrib_list) : EGL_NO_CONTEXT;
    if (c != EGL_NO_CONTEXT) {
        pthread_mutex_lock(&g_lock);
        /* remember first/primary context too, in case MakeCurrent record is missing */
        g_disp = dpy;
        g_ctx = c;
        g_ensured = 0; /* new context -> allow one auto-rebind if needed */
        pthread_mutex_unlock(&g_lock);
        fx_log("eglCreateContext d=%p cfg=%p share=%p -> %p", dpy, config, share_context, c);
    }
    return c;
}

EGLBoolean eglDestroyContext(EGLDisplay dpy, EGLContext ctx) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLContext) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLContext))sym("eglDestroyContext");
    pthread_mutex_lock(&g_lock);
    if (ctx == g_ctx) {
        g_ctx = EGL_NO_CONTEXT;
        g_surface_alive = 0;
        fx_log("eglDestroyContext: recorded ctx %p destroyed", ctx);
    }
    pthread_mutex_unlock(&g_lock);
    return f ? f(dpy, ctx) : EGL_FALSE;
}

EGLContext eglGetCurrentContext(void) {
    resolve();
    if (!R_eglGetCurrentContext) R_eglGetCurrentContext = (real_eglGetCurrentContext_t)sym("eglGetCurrentContext");
    return R_eglGetCurrentContext ? R_eglGetCurrentContext() : EGL_NO_CONTEXT;
}

EGLDisplay eglGetCurrentDisplay(void) {
    resolve();
    static EGLDisplay (*f)(void) = NULL;
    if (!f) f = (EGLDisplay (*)(void))sym("eglGetCurrentDisplay");
    return f ? f() : EGL_NO_DISPLAY;
}

EGLSurface eglGetCurrentSurface(EGLint readdraw) {
    resolve();
    static EGLSurface (*f)(EGLint) = NULL;
    if (!f) f = (EGLSurface (*)(EGLint))sym("eglGetCurrentSurface");
    return f ? f(readdraw) : EGL_NO_SURFACE;
}

EGLBoolean eglQueryContext(EGLDisplay dpy, EGLContext ctx, EGLint attribute, EGLint *value) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLContext, EGLint, EGLint *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLContext, EGLint, EGLint *))sym("eglQueryContext");
    return f ? f(dpy, ctx, attribute, value) : EGL_FALSE;
}

EGLBoolean eglWaitClient(void) {
    resolve();
    static EGLBoolean (*f)(void) = NULL;
    if (!f) f = (EGLBoolean (*)(void))sym("eglWaitClient");
    return f ? f() : EGL_FALSE;
}

EGLBoolean eglWaitGL(void) {
    resolve();
    static EGLBoolean (*f)(void) = NULL;
    if (!f) f = (EGLBoolean (*)(void))sym("eglWaitGL");
    return f ? f() : EGL_FALSE;
}

EGLBoolean eglWaitNative(EGLint engine) {
    resolve();
    static EGLBoolean (*f)(EGLint) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLint))sym("eglWaitNative");
    return f ? f(engine) : EGL_FALSE;
}

EGLBoolean eglSwapBuffers(EGLDisplay dpy, EGLSurface surface) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSurface) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface))sym("eglSwapBuffers");
    return f ? f(dpy, surface) : EGL_FALSE;
}

EGLBoolean eglCopyBuffers(EGLDisplay dpy, EGLSurface surface, EGLNativePixmapType target) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSurface, EGLNativePixmapType) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface, EGLNativePixmapType))sym("eglCopyBuffers");
    return f ? f(dpy, surface, target) : EGL_FALSE;
}

EGLBoolean eglBindAPI(EGLenum api) {
    resolve();
    static EGLBoolean (*f)(EGLenum) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLenum))sym("eglBindAPI");
    return f ? f(api) : EGL_FALSE;
}

EGLenum eglQueryAPI(void) {
    resolve();
    static EGLenum (*f)(void) = NULL;
    if (!f) f = (EGLenum (*)(void))sym("eglQueryAPI");
    return f ? f() : EGL_NONE;
}

EGLint eglGetError(void) {
    resolve();
    if (!R_eglGetError) R_eglGetError = (real_eglGetError_t)sym("eglGetError");
    return R_eglGetError ? R_eglGetError() : 0;
}

EGLBoolean eglReleaseThread(void) {
    resolve();
    static EGLBoolean (*f)(void) = NULL;
    if (!f) f = (EGLBoolean (*)(void))sym("eglReleaseThread");
    fx_log("eglReleaseThread called");
    return f ? f() : EGL_FALSE;
}

EGLBoolean eglGetVersion(EGLDisplay dpy, EGLint *major, EGLint *minor) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLint *, EGLint *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLint *, EGLint *))sym("eglGetVersion");
    return f ? f(dpy, major, minor) : EGL_FALSE;
}

EGLBoolean eglWaitSync(EGLDisplay dpy, EGLSync sync, EGLint flags) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSync, EGLint) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSync, EGLint))sym("eglWaitSync");
    return f ? f(dpy, sync, flags) : EGL_FALSE;
}

EGLBoolean eglQueryDisplayAttrib(EGLDisplay dpy, EGLint attribute, EGLAttrib *value) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLint, EGLAttrib *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLint, EGLAttrib *))sym("eglQueryDisplayAttrib");
    return f ? f(dpy, attribute, value) : EGL_FALSE;
}

EGLBoolean eglQueryDevicesEXT(EGLint max_devices, EGLDeviceEXT *devices, EGLint *num_devices) {
    resolve();
    static EGLBoolean (*f)(EGLint, EGLDeviceEXT *, EGLint *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLint, EGLDeviceEXT *, EGLint *))sym("eglQueryDevicesEXT");
    return f ? f(max_devices, devices, num_devices) : EGL_FALSE;
}

const char *eglQueryDeviceStringEXT(EGLDeviceEXT device, EGLint name) {
    resolve();
    static const char *(*f)(EGLDeviceEXT, EGLint) = NULL;
    if (!f) f = (const char *(*)(EGLDeviceEXT, EGLint))sym("eglQueryDeviceStringEXT");
    return f ? f(device, name) : NULL;
}

EGLImage eglCreateImage(EGLDisplay dpy, EGLContext ctx, EGLenum target, EGLClientBuffer buffer, const EGLAttrib *attrib_list) {
    resolve();
    static EGLImage (*f)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLAttrib *) = NULL;
    if (!f) f = (EGLImage (*)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLAttrib *))sym("eglCreateImage");
    return f ? f(dpy, ctx, target, buffer, attrib_list) : EGL_NO_IMAGE;
}

EGLImage eglCreateImageKHR(EGLDisplay dpy, EGLContext ctx, EGLenum target, EGLClientBuffer buffer, const EGLint *attrib_list) {
    resolve();
    static EGLImage (*f)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLint *) = NULL;
    if (!f) f = (EGLImage (*)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLint *))sym("eglCreateImageKHR");
    return f ? f(dpy, ctx, target, buffer, attrib_list) : EGL_NO_IMAGE;
}

EGLBoolean eglDestroyImage(EGLDisplay dpy, EGLImage image) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLImage) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLImage))sym("eglDestroyImage");
    return f ? f(dpy, image) : EGL_FALSE;
}

EGLBoolean eglDestroyImageKHR(EGLDisplay dpy, EGLImageKHR image) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLImageKHR) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLImageKHR))sym("eglDestroyImageKHR");
    return f ? f(dpy, image) : EGL_FALSE;
}

EGLSync eglCreateSync(EGLDisplay dpy, EGLenum type, const EGLAttrib *attrib_list) {
    resolve();
    static EGLSync (*f)(EGLDisplay, EGLenum, const EGLAttrib *) = NULL;
    if (!f) f = (EGLSync (*)(EGLDisplay, EGLenum, const EGLAttrib *))sym("eglCreateSync");
    return f ? f(dpy, type, attrib_list) : EGL_NO_SYNC;
}

EGLSync eglCreateSyncKHR(EGLDisplay dpy, EGLenum type, const EGLint *attrib_list) {
    resolve();
    static EGLSync (*f)(EGLDisplay, EGLenum, const EGLint *) = NULL;
    if (!f) f = (EGLSync (*)(EGLDisplay, EGLenum, const EGLint *))sym("eglCreateSyncKHR");
    return f ? f(dpy, type, attrib_list) : EGL_NO_SYNC;
}

EGLBoolean eglDestroySync(EGLDisplay dpy, EGLSync sync) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSync) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSync))sym("eglDestroySync");
    return f ? f(dpy, sync) : EGL_FALSE;
}

EGLBoolean eglDestroySyncKHR(EGLDisplay dpy, EGLSyncKHR sync) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSyncKHR) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSyncKHR))sym("eglDestroySyncKHR");
    return f ? f(dpy, sync) : EGL_FALSE;
}

EGLint eglClientWaitSync(EGLDisplay dpy, EGLSync sync, EGLint flags, EGLTime timeout) {
    resolve();
    static EGLint (*f)(EGLDisplay, EGLSync, EGLint, EGLTime) = NULL;
    if (!f) f = (EGLint (*)(EGLDisplay, EGLSync, EGLint, EGLTime))sym("eglClientWaitSync");
    return f ? f(dpy, sync, flags, timeout) : EGL_FALSE;
}

EGLint eglClientWaitSyncKHR(EGLDisplay dpy, EGLSyncKHR sync, EGLint flags, EGLTimeKHR timeout) {
    resolve();
    static EGLint (*f)(EGLDisplay, EGLSyncKHR, EGLint, EGLTimeKHR) = NULL;
    if (!f) f = (EGLint (*)(EGLDisplay, EGLSyncKHR, EGLint, EGLTimeKHR))sym("eglClientWaitSyncKHR");
    return f ? f(dpy, sync, flags, timeout) : EGL_FALSE;
}

EGLBoolean eglGetSyncAttrib(EGLDisplay dpy, EGLSync sync, EGLint attribute, EGLAttrib *value) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSync, EGLint, EGLAttrib *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSync, EGLint, EGLAttrib *))sym("eglGetSyncAttrib");
    return f ? f(dpy, sync, attribute, value) : EGL_FALSE;
}

EGLBoolean eglGetSyncAttribKHR(EGLDisplay dpy, EGLSyncKHR sync, EGLint attribute, EGLint *value) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSyncKHR, EGLint, EGLint *) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSyncKHR, EGLint, EGLint *))sym("eglGetSyncAttribKHR");
    return f ? f(dpy, sync, attribute, value) : EGL_FALSE;
}

EGLBoolean eglSignalSync(EGLDisplay dpy, EGLSync sync, EGLenum mode) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSync, EGLenum) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSync, EGLenum))sym("eglSignalSync");
    return f ? f(dpy, sync, mode) : EGL_FALSE;
}

EGLBoolean eglSignalSyncKHR(EGLDisplay dpy, EGLSyncKHR sync, EGLenum mode) {
    resolve();
    static EGLBoolean (*f)(EGLDisplay, EGLSyncKHR, EGLenum) = NULL;
    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSyncKHR, EGLenum))sym("eglSignalSyncKHR");
    return f ? f(dpy, sync, mode) : EGL_FALSE;
}

/* ------------------------------------------------------------------ */
/* constructor: log that we are loaded                                 */
/* ------------------------------------------------------------------ */
__attribute__((constructor)) static void fx_init(void) {
    char buf[128];
    snprintf(buf, sizeof(buf), "eglfix shim loaded (pid=%d)", (int)getpid());
    fx_log("%s", buf);
}
