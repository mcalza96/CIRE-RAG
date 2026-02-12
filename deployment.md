# Guía de Despliegue: Lanzamiento de JIT Data Analytics

Para que tu plataforma sea accesible en todo el mundo, usaremos **Vercel** por su integración nativa con Next.js.

## 1. Preparación en GitHub
1. Crea un nuevo repositorio en GitHub (ej: `jit-data-analytics`).
2. Sube tu código:
   ```bash
   git add .
   git commit -m "Identity Shift - JIT Data Analytics"
   git push origin main
   ```

## 2. Configuración en Vercel
1. Ve a [vercel.com](https://vercel.com) e importa tu repositorio.
2. En la sección **Environment Variables**, debes copiar exactamente los valores de tu `.env.local`:

| Variable | Descripción |
| :--- | :--- |
| `NEXT_PUBLIC_SUPABASE_URL` | La URL de tu proyecto en Supabase. |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | La llave pública anónima de Supabase. |
| `SUPABASE_SERVICE_ROLE_KEY` | Para acciones administrativas de alta seguridad. |
| `ADMIN_EMAIL` | El email que tendrá acceso al panel `/admin` (ej: `mca@test.com`). |

3. Haz clic en **Deploy**. ¡Vercel generará una URL pública para ti!

## 3. Configuración en Supabase (Crucial)
Para que el login funcione en producción, debes añadir la URL de Vercel a la lista blanca de Supabase:
1. Ve a **Authentication > URL Configuration**.
2. Añade la URL que te dio Vercel (ej: `https://tu-proyecto.vercel.app`) en **Site URL** y **Redirect URLs**.

---

## 🚀 Checklist de Lanzamiento
- [ ] ¿Están las tablas SQL creadas en Supabase?
- [ ] ¿Están configuradas las políticas RLS para JIT Data?
- [ ] ¿Has configurado el `ADMIN_EMAIL` correcto?
- [ ] ¿Has probado el flujo de Magic Link en el dominio de producción (`jitdata.cl`)?

¡Felicidades por lanzar la plataforma de JIT Data Analytics! 📊⚡️
