        /* =========================================================================
           1. DECOUPLED DETERMINISTIC FINANCIAL ENGINE (EXACT INTEGER CENTS IN MXN)
           ========================================================================= */
        const NuraFinancialEngine = {
            formatMXNFromCents(cents) {
                const sign = cents < 0 ? '-' : '';
                const abs = Math.abs(Math.round(Number(cents) || 0));
                const pesos = Math.floor(abs / 100);
                const centavos = String(abs % 100).padStart(2, '0');
                return `${sign}$${pesos.toLocaleString('en-US')}.${centavos} MXN`;
            },

            monthsToWeeks(months) {
                const m = Math.max(1, Math.min(24, parseInt(months, 10) || 1));
                if (m <= 4) return m * 4;
                return Math.min(104, Math.round((m * 52) / 12));
            },

            addDaysISO(isoDateStr, daysToAdd) {
                const parts = isoDateStr.split('-').map(Number);
                const dt = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
                dt.setUTCDate(dt.getUTCDate() + daysToAdd);
                return dt.toISOString().split('T')[0];
            },

            calculateFlatSchedule({
                principalPesos,
                monthlyRatePercent,
                assumedInterestMonths,
                termWeeks,
                firstPaymentDateISO,
                interestCalculationMethod
            }) {
                let resolvedMethod = interestCalculationMethod;
                if (!resolvedMethod) {
                    const origModal = document.getElementById('loan-origination-modal');
                    const isOrigOpen = origModal && !origModal.classList.contains('hidden');
                    resolvedMethod = isOrigOpen
                        ? (document.getElementById('lo-interest-method')?.value || 'FLAT_ORIGINAL_PRINCIPAL')
                        : (document.getElementById('sim-interest-method')?.value || 'FLAT_ORIGINAL_PRINCIPAL');
                }
                const method = String(resolvedMethod).trim().toUpperCase() === 'DECLINING_BALANCE'
                    ? 'DECLINING_BALANCE'
                    : 'FLAT_ORIGINAL_PRINCIPAL';

                const principalCents = Math.max(0, Math.round(Number(principalPesos) * 100));
                const rateBasisPoints = Math.max(0, Math.round(Number(monthlyRatePercent) * 100));
                const monthsCount = Math.max(1, parseInt(assumedInterestMonths, 10) || 4);
                const installmentsCount = Math.max(1, parseInt(termWeeks, 10) || 16);

                const schedule = [];
                let remainingPrincipalCents = principalCents;
                let accumulatedPrincipalCents = 0;
                let accumulatedInterestCents = 0;
                let monthlyInterestCents = 0;
                let totalInterestCents = 0;
                let totalRepaymentCents = 0;

                if (method === 'DECLINING_BALANCE' && rateBasisPoints > 0 && principalCents > 0) {
                    const periodicRate = (rateBasisPoints * monthsCount) / (10000.0 * installmentsCount);
                    const factor = Math.pow(1.0 + periodicRate, installmentsCount);
                    const regularPaymentCents = Math.round(principalCents * (periodicRate * factor) / (factor - 1.0));

                    for (let i = 1; i <= installmentsCount; i++) {
                        const isFinal = (i === installmentsCount);
                        const dueDate = this.addDaysISO(firstPaymentDateISO, (i - 1) * 7);
                        const beginningPrincipalCents = remainingPrincipalCents;

                        const interestComponentCents = Math.round(beginningPrincipalCents * periodicRate);
                        const principalComponentCents = isFinal
                            ? beginningPrincipalCents
                            : Math.max(0, Math.min(beginningPrincipalCents, regularPaymentCents - interestComponentCents));

                        const scheduledPaymentCents = principalComponentCents + interestComponentCents;
                        const endingPrincipalCents = beginningPrincipalCents - principalComponentCents;

                        accumulatedPrincipalCents += principalComponentCents;
                        accumulatedInterestCents += interestComponentCents;
                        remainingPrincipalCents = endingPrincipalCents;

                        schedule.push({
                            installmentNumber: i,
                            dueDate,
                            beginningPrincipalCents,
                            principalComponentCents,
                            interestComponentCents,
                            scheduledPaymentCents,
                            endingPrincipalCents
                        });
                    }

                    totalInterestCents = accumulatedInterestCents;
                    totalRepaymentCents = principalCents + totalInterestCents;
                    monthlyInterestCents = Math.round(totalInterestCents / monthsCount);
                } else {
                    monthlyInterestCents = Math.round((principalCents * rateBasisPoints) / 10000);
                    totalInterestCents = monthlyInterestCents * monthsCount;
                    totalRepaymentCents = principalCents + totalInterestCents;

                    const regularPrincipalCents = Math.floor(principalCents / installmentsCount);
                    const regularInterestCents = Math.floor(totalInterestCents / installmentsCount);

                    for (let i = 1; i <= installmentsCount; i++) {
                        const isFinal = (i === installmentsCount);
                        const dueDate = this.addDaysISO(firstPaymentDateISO, (i - 1) * 7);
                        const beginningPrincipalCents = remainingPrincipalCents;

                        const principalComponentCents = isFinal
                            ? (principalCents - accumulatedPrincipalCents)
                            : regularPrincipalCents;

                        const interestComponentCents = isFinal
                            ? (totalInterestCents - accumulatedInterestCents)
                            : regularInterestCents;

                        const scheduledPaymentCents = principalComponentCents + interestComponentCents;
                        const endingPrincipalCents = beginningPrincipalCents - principalComponentCents;

                        accumulatedPrincipalCents += principalComponentCents;
                        accumulatedInterestCents += interestComponentCents;
                        remainingPrincipalCents = endingPrincipalCents;

                        schedule.push({
                            installmentNumber: i,
                            dueDate,
                            beginningPrincipalCents,
                            principalComponentCents,
                            interestComponentCents,
                            scheduledPaymentCents,
                            endingPrincipalCents
                        });
                    }
                }

                return {
                    interestCalculationMethod: method,
                    principalCents,
                    monthlyRatePercent: Number(monthlyRatePercent),
                    monthlyInterestCents,
                    monthsCount,
                    installmentsCount,
                    totalInterestCents,
                    totalRepaymentCents,
                    regularPaymentCents: schedule[0].scheduledPaymentCents,
                    finalPaymentCents: schedule[schedule.length - 1].scheduledPaymentCents,
                    firstPaymentDateISO,
                    estimatedPayoffDate: schedule[schedule.length - 1].dueDate,
                    schedule
                };
            }
        };
